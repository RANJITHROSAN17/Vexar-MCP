"""Python AST loader for FastMCP, official MCP SDK, and @tool-decorated agent tools."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TypedDict

from mcp_doctor.models import CodeSink, ParameterSpec, ToolSpec
from mcp_doctor.tokens import estimate_tool_tokens

_NOQA_RE = re.compile(r"#\s*noqa\s*:\s*([A-Za-z0-9_,\s]+)", re.IGNORECASE)
_PARAM_DOC_RE = re.compile(r"^\s*:?param\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$")
_GOOGLE_ARG_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*:\s*(.+)$")

_SKIP_PARAM_NAMES = frozenset({"self", "cls", "ctx", "context"})

_SHELL_CALLS = frozenset({"os.system", "os.popen", "pty.spawn", "eval", "exec"})
_SUBPROCESS_CALLS = frozenset(
    {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
    }
)
_HTTP_CALLS = frozenset(
    {
        "urllib.request.urlopen",
        "urlopen",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.request",
        "httpx.get",
        "httpx.post",
        "httpx.AsyncClient.get",
        "httpx.AsyncClient.post",
    }
)
_FS_DESTRUCTIVE_CALLS = frozenset(
    {
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "shutil.rmtree",
        "Path.unlink",
        "Path.write_text",
        "Path.write_bytes",
    }
)


_DESERIALIZATION_CALLS = frozenset(
    {
        "pickle.load",
        "pickle.loads",
        "marshal.load",
        "marshal.loads",
        "shelve.open",
        "yaml.load",
        "yaml.load_all",
        "yaml.unsafe_load",
        "yaml.unsafe_load_all",
    }
)
_SAFE_YAML_LOADERS = frozenset(
    {
        "yaml.SafeLoader",
        "yaml.CSafeLoader",
        "yaml.loader.SafeLoader",
        "yaml.cyaml.CSafeLoader",
    }
)


def _import_aliases(statements: list[ast.stmt]) -> dict[str, str]:
    """Resolve direct imports without importing or executing inspected source."""
    aliases: dict[str, str] = {}
    for statement in statements:
        if isinstance(statement, ast.Import):
            for imported in statement.names:
                root = imported.name.split(".")[0]
                aliases[imported.asname or root] = imported.name if imported.asname else root
        elif isinstance(statement, ast.ImportFrom) and statement.module and statement.level == 0:
            for imported in statement.names:
                if imported.name != "*":
                    aliases[imported.asname or imported.name] = (
                        f"{statement.module}.{imported.name}"
                    )
    return aliases


def _resolve_imported_name(node: ast.expr, aliases: dict[str, str]) -> str:
    name = _call_qualname(node)
    root, separator, member = name.partition(".")
    return aliases[root] + separator + member if root in aliases else ""


def _unsafe_deserialization(node: ast.Call, aliases: dict[str, str]) -> bool:
    name = _resolve_imported_name(node.func, aliases)
    if name not in _DESERIALIZATION_CALLS:
        return False
    if name in {"yaml.load", "yaml.load_all"}:
        loader = next((kw.value for kw in node.keywords if kw.arg == "Loader"), None)
        if loader is None and len(node.args) > 1:
            loader = node.args[1]
        if loader is not None and _resolve_imported_name(loader, aliases) in _SAFE_YAML_LOADERS:
            return False
    return True


def _call_qualname(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_qualname(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _is_tool_decorator(dec: ast.expr) -> tuple[bool, str | None, str | None]:
    """Return (is_tool, custom_name, custom_description) for a function decorator."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    qual = _call_qualname(target)
    leaf = qual.split(".")[-1] if qual else ""
    if leaf not in {"tool", "mcp_tool", "function_tool"}:
        return False, None, None

    custom_name: str | None = None
    custom_desc: str | None = None
    if isinstance(dec, ast.Call):
        if (
            dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ):
            custom_name = dec.args[0].value
        for kw in dec.keywords:
            if (
                kw.arg == "name"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                custom_name = kw.value.value
            elif (
                kw.arg == "description"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                custom_desc = kw.value.value
    return True, custom_name, custom_desc


def _extract_param_docs(docstring: str) -> dict[str, str]:
    docs: dict[str, str] = {}
    in_args_section = False
    for raw_line in docstring.splitlines():
        stripped = raw_line.strip()
        if stripped in {"Args:", "Arguments:", "Parameters:"}:
            in_args_section = True
            continue
        if stripped.endswith(":") and stripped[:-1] in {
            "Returns",
            "Raises",
            "Yields",
            "Examples",
            "Notes",
        }:
            in_args_section = False
            continue

        m_sphinx = _PARAM_DOC_RE.match(raw_line)
        if m_sphinx:
            docs[m_sphinx.group(1)] = m_sphinx.group(2).strip()
            continue

        if in_args_section:
            m_google = _GOOGLE_ARG_RE.match(raw_line)
            if m_google:
                docs[m_google.group(1)] = m_google.group(2).strip()
    return docs


def _annotation_info(ann: ast.expr | None) -> tuple[str | None, bool]:
    """Return (normalized_type_string, has_explicit_bounds)."""
    if ann is None:
        return None, False
    rendered = ast.unparse(ann)
    has_bounds = any(
        token in rendered
        for token in (
            "max_length",
            "max_items",
            "le=",
            "lt=",
            "ge=",
            "gt=",
            "Literal[",
            "MaxLen",
        )
    )
    return rendered, has_bounds


def _referenced_names(node: ast.AST) -> set[str]:
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


def _body_has_url_validation(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    src = ast.unparse(func)
    return any(
        marker in src
        for marker in (
            "urlparse",
            "urlsplit",
            "ALLOWED_HOSTS",
            "allowlist",
            "startswith(('https://'",
        )
    )


def _body_has_path_confinement(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    src = ast.unparse(func)
    return any(
        marker in src
        for marker in (".resolve()", "is_relative_to", "commonpath", "BASE_DIR", "SAFE_DIR")
    )


def _body_clamps_param(func: ast.FunctionDef | ast.AsyncFunctionDef, param_name: str) -> bool:
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and _call_qualname(node.func) == "min"
            and param_name in _referenced_names(node)
        ):
            return True
        if isinstance(node, ast.Compare) and param_name in _referenced_names(node):
            return True
    return False


def _extract_sinks(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    param_names: set[str],
    module_aliases: dict[str, str],
) -> tuple[CodeSink, ...]:
    sinks: list[CodeSink] = []
    has_url_guard = _body_has_url_validation(func)
    has_path_guard = _body_has_path_confinement(func)
    # Local imports belong to this handler only; do not leak aliases between tools.
    aliases = {name: value for name, value in module_aliases.items() if name not in param_names}
    aliases.update(_import_aliases(func.body))

    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        qual = _call_qualname(node.func)
        used_params = sorted(_referenced_names(node) & param_names)
        first_param = used_params[0] if used_params else None

        if _unsafe_deserialization(node, aliases):
            sinks.append(
                CodeSink(
                    kind="deserialization",
                    call_name=qual,
                    line=node.lineno,
                    uses_param=first_param,
                )
            )
        elif qual in _SHELL_CALLS:
            sinks.append(
                CodeSink(
                    kind="shell",
                    call_name=qual,
                    line=node.lineno,
                    uses_param=first_param,
                    validated=False,
                )
            )
        elif qual in _SUBPROCESS_CALLS:
            shell_true = any(
                kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
            )
            if shell_true:
                sinks.append(
                    CodeSink(
                        kind="shell",
                        call_name=f"{qual}(shell=True)",
                        line=node.lineno,
                        uses_param=first_param,
                        validated=False,
                    )
                )
        elif (qual in _HTTP_CALLS or qual.endswith((".get", ".post"))) and first_param:
            if qual in _HTTP_CALLS or any(
                client in qual for client in ("requests", "httpx", "urllib", "client", "session")
            ):
                timeout_missing = True
                for kw in node.keywords:
                    if kw.arg == "timeout":
                        if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                            timeout_missing = True
                        else:
                            timeout_missing = False
                        break
                sinks.append(
                    CodeSink(
                        kind="ssrf",
                        call_name=qual,
                        line=node.lineno,
                        uses_param=first_param,
                        validated=has_url_guard,
                        timeout_missing=timeout_missing,
                    )
                )
        elif qual == "open" and first_param:
            is_write = False
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
                is_write = any(flag in mode for flag in ("w", "a", "x", "+"))
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
                    is_write = any(flag in mode for flag in ("w", "a", "x", "+"))
            # Even read-mode open on unvalidated user paths can leak `/etc/passwd` or secrets
            sinks.append(
                CodeSink(
                    kind="filesystem",
                    call_name="open(write)" if is_write else "open",
                    line=node.lineno,
                    uses_param=first_param,
                    validated=has_path_guard,
                )
            )
        elif (
            qual in _FS_DESTRUCTIVE_CALLS
            or qual.endswith((".write_text", ".write_bytes", ".unlink"))
        ) and first_param:
            sinks.append(
                CodeSink(
                    kind="filesystem",
                    call_name=qual,
                    line=node.lineno,
                    uses_param=first_param,
                    validated=has_path_guard,
                )
            )

    return tuple(sinks)


def _extract_suppressions(lines: list[str], start_line: int, end_line: int) -> frozenset[str]:
    suppressed: set[str] = set()
    lo = max(0, start_line - 2)
    hi = min(len(lines), end_line)
    for line in lines[lo:hi]:
        m = _NOQA_RE.search(line)
        if m:
            for code in m.group(1).split(","):
                cleaned = code.strip().upper()
                if cleaned:
                    suppressed.add(cleaned)
    return frozenset(suppressed)


class _ModelField(TypedDict):
    name: str
    type_str: str | None
    description: str
    required: bool
    has_bounds: bool
    line: int


def _build_base_index(tree: ast.AST) -> dict[str, list[_ModelField]]:
    """Index module-level BaseModel / TypedDict classes and their fields."""
    index: dict[str, list[_ModelField]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_model = False
        for base in node.bases:
            if (
                isinstance(base, ast.Name)
                and base.id in ("BaseModel", "TypedDict")
                or isinstance(base, ast.Attribute)
                and base.attr in ("BaseModel", "TypedDict")
            ):
                is_model = True
        if not is_model:
            continue
        fields: list[_ModelField] = []
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                name = item.target.id
                ann_str = ast.unparse(item.annotation) if item.annotation else None
                desc = ""
                bounds = False
                if isinstance(item.value, ast.Call):
                    call_func = item.value.func
                    is_field = False
                    if (
                        isinstance(call_func, ast.Name)
                        and call_func.id == "Field"
                        or isinstance(call_func, ast.Attribute)
                        and call_func.attr == "Field"
                    ):
                        is_field = True
                    if is_field:
                        for kw in item.value.keywords:
                            if (
                                kw.arg == "description"
                                and isinstance(kw.value, ast.Constant)
                                and isinstance(kw.value.value, str)
                            ):
                                desc = kw.value.value
                            if kw.arg in ("le", "lt", "ge", "gt", "max_length", "min_length"):
                                bounds = True
                if ann_str and any(
                    tok in ann_str
                    for tok in ("max_length", "le=", "lt=", "ge=", "gt=", "Literal[", "MaxLen")
                ):
                    bounds = True
                fields.append(
                    {
                        "name": name,
                        "type_str": ann_str,
                        "description": desc,
                        "required": item.value is None,
                        "has_bounds": bounds,
                        "line": item.lineno,
                    }
                )
        if fields:
            index[node.name] = fields
    return index


def load_python_file(path: Path) -> list[ToolSpec]:
    """Parse a Python source file and return all discovered MCP / agent ToolSpecs."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    base_index = _build_base_index(tree)
    module_aliases = _import_aliases(tree.body)

    tools: list[ToolSpec] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            matched = False
            custom_name: str | None = None
            custom_desc: str | None = None
            for dec in node.decorator_list:
                ok, dec_name, dec_desc = _is_tool_decorator(dec)
                if ok:
                    matched = True
                    custom_name = dec_name or custom_name
                    custom_desc = dec_desc or custom_desc
            if not matched:
                continue

            tool_name = custom_name or node.name
            docstring = ast.get_docstring(node) or ""
            description = custom_desc if custom_desc is not None else docstring
            param_docs = _extract_param_docs(docstring)

            all_args = (
                list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
            )
            num_pos = len(node.args.posonlyargs) + len(node.args.args)
            num_defaults = len(node.args.defaults)
            default_start = num_pos - num_defaults

            params: list[ParameterSpec] = []
            user_param_names: set[str] = set()

            for idx, arg in enumerate(all_args):
                if arg.arg in _SKIP_PARAM_NAMES:
                    continue
                type_str, ann_bounded = _annotation_info(arg.annotation)
                if type_str and any(ctx_t in type_str for ctx_t in ("Context", "RequestContext")):
                    continue
                user_param_names.add(arg.arg)
                if idx < num_pos:
                    has_default = idx >= default_start
                else:
                    kw_idx = idx - num_pos
                    has_default = (
                        kw_idx < len(node.args.kw_defaults)
                        and node.args.kw_defaults[kw_idx] is not None
                    )
                clamped = ann_bounded or _body_clamps_param(node, arg.arg)
                matched_class = None
                if type_str:
                    base_name = type_str.split(".")[-1].split("[")[0].split("]")[0]
                    if base_name in base_index:
                        matched_class = base_name
                if matched_class:
                    for fld in base_index[matched_class]:
                        params.append(
                            ParameterSpec(
                                name=f"{arg.arg}.{fld['name']}",
                                type_name=fld.get("type_str"),
                                description=fld.get("description", "")
                                or param_docs.get(arg.arg, ""),
                                required=fld["required"] if not has_default else False,
                                has_bounds=fld["has_bounds"] or clamped,
                                line=fld.get("line", arg.lineno),
                            )
                        )
                else:
                    params.append(
                        ParameterSpec(
                            name=arg.arg,
                            type_name=type_str,
                            description=param_docs.get(arg.arg, ""),
                            required=not has_default,
                            has_bounds=clamped,
                            line=arg.lineno,
                        )
                    )

            sinks = _extract_sinks(node, user_param_names, module_aliases)
            end_lineno = getattr(node, "end_lineno", node.lineno) or node.lineno
            suppressed = _extract_suppressions(lines, node.lineno, end_lineno)
            param_tuple = tuple(params)
            est_tokens = estimate_tool_tokens(tool_name, description, param_tuple)

            tools.append(
                ToolSpec(
                    name=tool_name,
                    description=description,
                    source_file=str(path),
                    line=node.lineno,
                    parameters=param_tuple,
                    sinks=sinks,
                    suppressed_rules=suppressed,
                    estimated_tokens=est_tokens,
                )
            )

        elif isinstance(node, ast.Call) and _call_qualname(node.func).split(".")[-1] == "Tool":
            kw_map = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            name_node = kw_map.get("name")
            if not (isinstance(name_node, ast.Constant) and isinstance(name_node.value, str)):
                continue
            desc_node = kw_map.get("description")
            desc_val = (
                desc_node.value
                if isinstance(desc_node, ast.Constant) and isinstance(desc_node.value, str)
                else ""
            )
            end_lineno = getattr(node, "end_lineno", node.lineno) or node.lineno
            suppressed = _extract_suppressions(lines, node.lineno, end_lineno)
            est_tokens = estimate_tool_tokens(name_node.value, desc_val, ())
            tools.append(
                ToolSpec(
                    name=name_node.value,
                    description=desc_val,
                    source_file=str(path),
                    line=node.lineno,
                    suppressed_rules=suppressed,
                    estimated_tokens=est_tokens,
                )
            )

    tools.sort(key=lambda t: t.line)
    return tools
