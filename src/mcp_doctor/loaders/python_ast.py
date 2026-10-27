"""Python AST loader for FastMCP, official MCP SDK, and @tool-decorated agent tools."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TypedDict

from mcp_doctor.models import CodeSink, ParameterSpec, SecretLiteral, ToolSpec
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

# ---------------------------------------------------------------------------
# MCP009: hardcoded credential extraction.
#
# Only high-confidence provider prefixes and literals assigned to sensitive names
# are collected. The literal itself is never copied into ToolSpec (see the
# SecretLiteral model), so a scan report cannot leak the credential it flags.
# ---------------------------------------------------------------------------
_SECRET_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GitHub personal access token ('ghp_')", re.compile(r"ghp_[A-Za-z0-9]+")),
    ("GitHub fine-grained token ('github_pat_')", re.compile(r"github_pat_[A-Za-z0-9_]+")),
    ("OpenAI/Stripe-style key ('sk-')", re.compile(r"\bsk-[A-Za-z0-9_-]+")),
    ("AWS access key ID ('AKIA')", re.compile(r"\bAKIA[0-9A-Z]+")),
    ("Slack token ('xoxb-')", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+")),
    ("Google API key ('AIza')", re.compile(r"\bAIza[0-9A-Za-z_-]{10,}")),
    ("GitLab personal access token ('glpat-')", re.compile(r"\bglpat-[A-Za-z0-9_-]+")),
    ("Bearer credential", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("PEM private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

# Whole-value placeholders ("YOUR_API_KEY", "example", "<token>", "${TOKEN}") and
# explicit env/secret-manager references must never be reported as leaked keys.
_PLACEHOLDER_RE = re.compile(
    r"(?i)(\byour\b|\byours\b|\bexample\b|\bplaceholder\b|\bdummy\b|\bchangeme\b|"
    r"\breplace[_-]?me\b|\bredacted\b|\bsample\b|\bfake\b|\btest\b|\btodo\b|\bnone\b|"
    r"\bnull\b|\bundefined\b|<[^>]*>|\$\{[^}]*\}|\{\{[^}]*\}\}|\{[a-z_]+\})"
)
_COMPACT_PLACEHOLDER_MARKERS = (
    "yourapikey",
    "yourkey",
    "yoursecret",
    "yourtoken",
    "yourpassword",
    "skexample",
    "ghpexample",
    "akiaexample",
    "xoxbexample",
    "bearerexample",
    "notareal",
    "insertkey",
    "sometoken",
    "testtoken",
    "testkey",
    "replacewith",
    "changeme",
    "redacted",
    "placeholder",
    "secretmanager",
    "fromenv",
    "getenv",
    "osenviron",
    "processenv",
)
_ENV_REFERENCE_RE = re.compile(
    r"(?i)(os\.environ|os\.getenv|getenv|process\.env|environ\b|vault:|"
    r"secretmanager|from[_-]?env|env[_-]?var)"
)
_SECRET_TARGET_SUFFIXES = (
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "accesskey",
    "privatekey",
    "clientsecret",
    "authtoken",
    "authorization",
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


def _is_placeholder(value: str) -> bool:
    """Return True for documentation placeholders, templates and env references.

    Placeholder filtering is applied before a provider prefix is reported, so
    examples such as `sk-example123` or `Bearer YOUR_API_KEY` stay clean.
    """
    text = value.strip()
    if not text:
        return True
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    return bool(
        _PLACEHOLDER_RE.search(text)
        or _ENV_REFERENCE_RE.search(text)
        or any(marker in compact for marker in _COMPACT_PLACEHOLDER_MARKERS)
    )


def _looks_like_credential(value: str) -> bool:
    """Guard against flagging config values (paths, URLs, env-var names) as secrets."""
    text = value.strip()
    if len(text) < 8 or _is_placeholder(text):
        return False
    if text.startswith(("http://", "https://", "/", "./", "os.", "$", "{", "<", "env:")):
        return False
    if "{" in text or "}" in text:
        return False
    # ALL_CAPS_WITH_UNDERSCORES is an env-var *name*, not a credential value.
    return not ("_" in text and text.upper() == text and text.replace("_", "").isalnum())


def _is_sensitive_target(name: str) -> bool:
    """Match `api_key`, `client_secret`, `auth_token`, `Authorization`, etc."""
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return normalized == "key" or normalized.endswith(_SECRET_TARGET_SUFFIXES)


def _assignment_target_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _assignment_target_name(node.value)
    return ""


def _remember_secret_value(value: ast.expr, target: str, found: dict[int, str]) -> None:
    if (
        _is_sensitive_target(target)
        and isinstance(value, ast.Constant)
        and isinstance(value.value, str)
    ):
        found[id(value)] = target


def _sensitive_value_targets(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[int, str]:
    """Map string-literal node ids to the sensitive name that receives them."""
    found: dict[int, str] = {}
    for statement in func.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    _remember_secret_value(node.value, _assignment_target_name(target), found)
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
                value = node.value
                if value is not None:
                    _remember_secret_value(value, _assignment_target_name(node.target), found)
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg:
                        _remember_secret_value(keyword.value, keyword.arg, found)
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=False):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        _remember_secret_value(value, key.value, found)
    return found


def _docstring_constants(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """Docstrings often document auth schemes; they are not credential literals."""
    ids: set[int] = set()
    for candidate in ast.walk(func):
        if not isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not candidate.body:
            continue
        first = candidate.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _extract_secret_literals(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[SecretLiteral, ...]:
    """Collect high-confidence hardcoded credentials from a decorated tool body.

    Two signals are used: known provider token prefixes anywhere in a string
    constant, and string literals handed to a secret-ish name. Decorators and
    docstrings are excluded because they commonly contain documentation examples.
    """
    docstrings = _docstring_constants(func)
    targets = _sensitive_value_targets(func)
    findings: list[SecretLiteral] = []
    seen: set[tuple[int, str, str]] = set()

    for statement in func.body:
        for node in ast.walk(statement):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docstrings:
                continue
            value = node.value
            label = next(
                (name for name, regex in _SECRET_TOKEN_PATTERNS if regex.search(value)), ""
            )
            if label and _is_placeholder(value):
                label = ""
            target = targets.get(id(node), "")
            if not label:
                if not target or not _looks_like_credential(value):
                    continue
                label = "credential-like string literal"
            key = (node.lineno, label, target)
            if key in seen:
                continue
            seen.add(key)
            findings.append(SecretLiteral(pattern=label, target=target, line=node.lineno))

    findings.sort(key=lambda item: (item.line, item.pattern, item.target))
    return tuple(findings)


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
            # MCP009: scan the same tool body for hardcoded credential literals.
            secret_literals = _extract_secret_literals(node)
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
                    secret_literals=secret_literals,
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
