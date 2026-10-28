"""Python AST loader for FastMCP, official MCP SDK, and @tool-decorated agent tools."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TypedDict

from mcp_doctor.models import (
    CodeSink,
    IssueKind,
    ParameterSpec,
    ParseIssue,
    SecretLiteral,
    ToolSpec,
)
from mcp_doctor.tokens import estimate_tool_tokens

_NOQA_RE = re.compile(r"#\s*noqa\s*:\s*([A-Za-z0-9_,\s]+)", re.IGNORECASE)
_PARAM_DOC_RE = re.compile(r"^\s*:?param\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$")
_GOOGLE_ARG_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*:\s*(.+)$")

_SKIP_PARAM_NAMES = frozenset({"self", "cls", "ctx", "context"})

# How many module-local helper calls deep taint is followed. One hop covers the common
# `_run_cmd(param)` / `_fetch(url)` delegation pattern; each further hop multiplies the
# chance of a finding nobody can explain from the code shown next to it.
_HELPER_HOP_LIMIT = 1

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
# MCP011: DB-API / SQLAlchemy execution entry points. Matched on the method name so
# cursors, sessions, connections and async variants are covered alike.
_SQL_EXECUTE_METHODS = frozenset({"execute", "executemany"})

# The statement must read like SQL before we call it SQL injection. Without this,
# `graphql_client.execute(f"query {name}")` and RPC/job `.execute()` calls would be
# reported as SQL injection false positives. Documented in docs/limitations.md.
_SQL_KEYWORDS = (
    "select",
    "insert",
    "update",
    "delete",
    "replace",
    "merge",
    "alter",
    "drop",
    "truncate",
    "create",
)

# Fallback for statements whose constant fragments carry no SQL keyword (for example a
# query assembled from a prefix variable): a database-shaped receiver is enough.
_SQL_RECEIVER_HINTS = (
    "cursor",
    "conn",
    "db",
    "engine",
    "session",
    "sql",
    "psycopg",
    "asyncpg",
    "sqlite",
    "alembic",
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


def _is_sql_execute_call(node: ast.Call) -> bool:
    """Return True when the call is a `.execute(...)`/`.executemany(...)` method call."""
    return isinstance(node.func, ast.Attribute) and node.func.attr in _SQL_EXECUTE_METHODS


def _interpolated_statement(node: ast.Call) -> ast.expr | None:
    """Return the first argument when it is built by interpolation instead of being literal.

    Covers f-strings (``ast.JoinedStr``), ``%``/``+`` concatenation (``ast.BinOp``) and
    ``"...".format(...)`` calls. A plain string literal, or a bare variable whose origin
    is unknown, returns None: parameters cannot be proven to reach the statement.
    """
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.JoinedStr):
        return first
    if isinstance(first, ast.BinOp) and isinstance(first.op, (ast.Add, ast.Mod)):
        return first
    if (
        isinstance(first, ast.Call)
        and isinstance(first.func, ast.Attribute)
        and first.func.attr == "format"
    ):
        return first
    return None


def _statement_literal_text(node: ast.AST) -> str:
    """Join the constant fragments of an interpolated statement for keyword sniffing."""
    return " ".join(
        sub.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    )


def _is_sql_statement(expression: ast.expr, call_qualname: str) -> bool:
    """Decide whether an interpolated `.execute()` argument is really a SQL statement.

    Reported as SQL only when the literal fragments contain a SQL keyword or the receiver
    is database-shaped. This keeps ``graphql_client.execute(f"query {name}")`` and generic
    job/RPC ``.execute()`` calls out of MCP011, which is an error-severity rule.
    """
    lowered = _statement_literal_text(expression).lower()
    if any(re.search(rf"\b{keyword}\b", lowered) for keyword in _SQL_KEYWORDS):
        return True
    receiver = call_qualname.rsplit(".", 1)[0].lower()
    return any(hint in receiver for hint in _SQL_RECEIVER_HINTS)


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


def _classify_call(
    node: ast.Call,
    qual: str,
    tainted: dict[str, str],
    aliases: dict[str, str],
    has_url_guard: bool,
    has_path_guard: bool,
    *,
    require_taint: bool = False,
) -> CodeSink | None:
    """Apply the sink table to a call whose qualifying name is already known."""
    used_params = sorted({tainted[name] for name in _referenced_names(node) if name in tainted})
    first_param = used_params[0] if used_params else None
    if require_taint and first_param is None:
        return None

    if _unsafe_deserialization(node, aliases):
        return CodeSink(
            kind="deserialization",
            call_name=qual,
            line=node.lineno,
            uses_param=first_param,
        )
    if _is_sql_execute_call(node):
        statement = _interpolated_statement(node)
        if statement is not None:
            tainted_args = sorted(
                {tainted[name] for name in _referenced_names(statement) if name in tainted}
            )
            if tainted_args and _is_sql_statement(statement, qual):
                return CodeSink(
                    kind="sql",
                    call_name=qual,
                    line=node.lineno,
                    uses_param=tainted_args[0],
                )
        return None
    if qual in _SHELL_CALLS:
        return CodeSink(
            kind="shell",
            call_name=qual,
            line=node.lineno,
            uses_param=first_param,
            validated=False,
        )
    if qual in _SUBPROCESS_CALLS:
        shell_true = any(
            kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in node.keywords
        )
        if shell_true:
            return CodeSink(
                kind="shell",
                call_name=f"{qual}(shell=True)",
                line=node.lineno,
                uses_param=first_param,
                validated=False,
            )
        return None
    if (qual in _HTTP_CALLS or qual.endswith((".get", ".post"))) and first_param:
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
            return CodeSink(
                kind="ssrf",
                call_name=qual,
                line=node.lineno,
                uses_param=first_param,
                validated=has_url_guard,
                timeout_missing=timeout_missing,
            )
        return None
    if qual == "open" and first_param:
        is_write = False
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            mode = str(node.args[1].value)
            is_write = any(flag in mode for flag in ("w", "a", "x", "+"))
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = str(kw.value.value)
                is_write = any(flag in mode for flag in ("w", "a", "x", "+"))
        # Even read-mode open on unvalidated user paths can leak `/etc/passwd` or secrets
        return CodeSink(
            kind="filesystem",
            call_name="open(write)" if is_write else "open",
            line=node.lineno,
            uses_param=first_param,
            validated=has_path_guard,
        )
    if (
        qual in _FS_DESTRUCTIVE_CALLS or qual.endswith((".write_text", ".write_bytes", ".unlink"))
    ) and first_param:
        return CodeSink(
            kind="filesystem",
            call_name=qual,
            line=node.lineno,
            uses_param=first_param,
            validated=has_path_guard,
        )
    return None


def _index_module_helpers(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Index module-level functions so tool bodies can be traced into them.

    Only top-level ``def``/``async def`` statements are indexed. Methods reachable through
    ``self.``, closures defined inside a tool, and functions imported from other modules are
    deliberately out of scope: following them requires type or inter-module analysis, and a
    guessed trace produces findings that cannot be explained to the person reading them.
    """
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            helpers[node.name] = node
    return helpers


def _helper_bindings(
    call: ast.Call,
    helper: ast.FunctionDef | ast.AsyncFunctionDef,
    tainted: dict[str, str],
) -> dict[str, str]:
    """Map the helper's parameters to the tool parameters passed into this call.

    Returns a mapping of *helper* parameter name to the *tool* parameter name it receives.
    Calls are matched positionally and by keyword. Anything the analysis cannot map — a
    ``*args``/``**kwargs`` spread whose target parameter is unknown — is skipped rather than
    guessed, because attributing a sink to the wrong parameter is worse than missing it.
    """
    positional = list(helper.args.posonlyargs) + list(helper.args.args)
    bindings: dict[str, str] = {}
    for index, value in enumerate(call.args):
        if isinstance(value, ast.Starred) or index >= len(positional):
            continue
        names = sorted({tainted[name] for name in _referenced_names(value) if name in tainted})
        if names:
            bindings[positional[index].arg] = names[0]
    declared = {arg.arg for arg in positional} | {arg.arg for arg in helper.args.kwonlyargs}
    for keyword in call.keywords:
        if keyword.arg is None or keyword.arg not in declared:
            continue
        names = sorted(
            {tainted[name] for name in _referenced_names(keyword.value) if name in tainted}
        )
        if names:
            bindings[keyword.arg] = names[0]
    return bindings


def _resolve_helper(
    call: ast.Call,
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    aliases: dict[str, str],
    tainted: dict[str, str],
) -> tuple[str, ast.FunctionDef | ast.AsyncFunctionDef] | None:
    """Return the module-local helper a call targets, if it targets one.

    A bare ``Name`` is required: an attribute such as ``self._run`` is a method, not a
    module-level helper. A name that is bound by an import, or shadowed by a tool parameter,
    is not the module function and must not be traced.
    """
    func = call.func
    if not isinstance(func, ast.Name):
        return None
    name = func.id
    if name in aliases or name in tainted or name not in helpers:
        return None
    return name, helpers[name]


def _extract_sinks(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    param_names: set[str],
    module_aliases: dict[str, str],
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] | None = None,
) -> tuple[CodeSink, ...]:
    """Extract sinks from a tool body, following calls into module-local helpers.

    Propagates taint one hop: when a tool passes a parameter into ``_run_cmd(cmd)`` and that
    helper contains a sink consuming its parameter, the sink is attributed to the tool under
    the parameter the tool's caller controls. Production MCP servers routinely delegate to
    private helpers, and without this a tool that shells out through ``_run_cmd`` looked
    clean.

    Three limits keep the result trustworthy:

    * **One hop.** The helper's own calls are scanned for sinks but not followed further.
      Deeper traces need inter-procedural dataflow, not name matching.
    * **Taint is required.** A sink inside a helper is only reported when it consumes a
      parameter the tool actually passed in. Otherwise every tool that calls a helper
      containing a constant ``os.system("ls")`` would inherit that finding.
    * **Recursion terminates.** ``visited`` records the helpers already expanded on this
      path, so mutually recursive helpers (``_a -> _b -> _a``) cannot loop even if the hop
      limit is raised later.
    """
    return tuple(
        _collect_sinks(func, {name: name for name in param_names}, module_aliases, helpers or {})
    )


def _collect_sinks(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    tainted: dict[str, str],
    module_aliases: dict[str, str],
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    *,
    visited: frozenset[str] = frozenset(),
    depth: int = 0,
    inherited_guards: frozenset[str] = frozenset(),
) -> list[CodeSink]:
    """Walk one function, returning its sinks and those of one hop of helpers."""
    sinks: list[CodeSink] = []
    has_url_guard = _body_has_url_validation(func) or "ssrf" in inherited_guards
    has_path_guard = _body_has_path_confinement(func) or "filesystem" in inherited_guards
    # Local imports belong to this handler only; do not leak aliases between tools.
    aliases = {name: value for name, value in module_aliases.items() if name not in tainted}
    aliases.update(_import_aliases(func.body))
    require_taint = depth > 0

    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        sink = _classify_call(
            node,
            _call_qualname(node.func),
            tainted,
            aliases,
            has_url_guard,
            has_path_guard,
            require_taint=require_taint,
        )
        if sink is not None:
            sinks.append(sink)

        if depth >= _HELPER_HOP_LIMIT:
            continue
        resolved = _resolve_helper(node, helpers, aliases, tainted)
        if resolved is None:
            continue
        helper_name, helper = resolved
        if helper_name in visited:
            continue
        bindings = _helper_bindings(node, helper, tainted)
        if not bindings:
            # The tool passed no parameter into the helper, so nothing is tainted downstream.
            continue
        sinks.extend(
            _collect_sinks(
                helper,
                bindings,
                module_aliases,
                helpers,
                visited=visited | {helper_name},
                depth=depth + 1,
                # A guard in the caller covers the callee: validating a URL before calling
                # `_fetch(url)` is real validation, even though the sink is inside `_fetch`.
                inherited_guards=inherited_guards
                | ({"ssrf"} if has_url_guard else frozenset())
                | ({"filesystem"} if has_path_guard else frozenset()),
            )
        )

    # A helper called twice with different parameters yields two honest findings; the same
    # sink reached twice along one call path does not.
    seen: set[tuple[str, str, int, str | None]] = set()
    unique: list[CodeSink] = []
    for sink in sinks:
        key = (sink.kind, sink.call_name, sink.line, sink.uses_param)
        if key not in seen:
            seen.add(key)
            unique.append(sink)
    return unique


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


def load_python_file(path: Path, *, issues: list[ParseIssue] | None = None) -> list[ToolSpec]:
    """Parse a Python source file and return all discovered MCP / agent ToolSpecs.

    Args:
        path: Python source file to inspect. It is parsed with ``ast.parse`` and never
            imported or executed.
        issues: Optional sink for analysis failures. A file that cannot be read or parsed
            appends a :class:`ParseIssue` here instead of silently yielding no tools, so
            the caller can report incomplete coverage rather than a clean result.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        if issues is not None:
            issues.append(
                ParseIssue(
                    source_file=str(path),
                    kind=IssueKind.UNREADABLE,
                    message=f"cannot read file: {exc}",
                )
            )
        return []
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError) as exc:
        if issues is not None:
            issues.append(
                ParseIssue(
                    source_file=str(path),
                    kind=IssueKind.SYNTAX_ERROR,
                    message=f"{type(exc).__name__}: {getattr(exc, 'msg', exc)}",
                    line=getattr(exc, "lineno", None),
                )
            )
        return []
    base_index = _build_base_index(tree)
    module_aliases = _import_aliases(tree.body)
    # Sinks delegated to module-local helpers are attributed to the tool that passes the
    # parameter in, so the index is built once per file rather than per tool.
    module_helpers = _index_module_helpers(tree)

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

            sinks = _extract_sinks(node, user_param_names, module_aliases, module_helpers)
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
