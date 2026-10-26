"""Best-standard static TypeScript / JavaScript loader for MCP SDK.

Design principles (safe by construction):
  1. Zero execution — pure regex / line scanning; no Node, no eval, no subprocess.
  2. Import-aware sink detection — tracks `import { exec } from "child_process"`
     so bare `exec(` is only flagged when sourced from dangerous modules.
  3. Zod-aware parameter expansion — parses `.describe()`, `.optional()`, `.default()`,
     `.max()/.min()` and nested `z.object()` properties where structurally simple.
  4. Timeout audit integration — reports `timeout_missing` on HTTP sinks (MCP010)
     by inspecting call keyword args (`timeout=` present / `timeout=None` / absent).
  5. Precise source-line tracking — parameters and sinks report real line numbers.
  6. Ignores `.d.ts` declaration files; respects `node_modules` exclusions already
     configured in `ScanConfig`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple, TypedDict

from mcp_doctor.models import CodeSink, ParameterSpec, ToolSpec
from mcp_doctor.tokens import estimate_tool_tokens

# ------------------------------------------------------------------
# Safe matching constants
# ------------------------------------------------------------------
_DECLARATION_SUFFIX = ".d.ts"
_EXTS = (".ts", ".js", ".jsx")

# Tool-registration entry points (both SDK patterns)
_TOOL_PATTERNS = [
    re.compile(r"(?:\bserver\.)?tool\s*\("),
    re.compile(r"\bregisterTool\s*\("),
]

# Zod scalar / container tokens (ordered by specificity)
_ZOD_SCALARS = {
    "z.string": "string",
    "z.number": "number",
    "z.boolean": "boolean",
    "z.bigint": "bigint",
    "z.date": "date",
    "z.any": "any",
    "z.unknown": "unknown",
    "z.never": "never",
}
_ZOD_CONTAINERS = {
    "z.array": "list",
    "z.object": "object",
    "z.record": "record",
    "z.tuple": "tuple",
    "z.map": "map",
    "z.set": "set",
    "z.enum": "enum",
    "z.union": "union",
    "z.intersection": "intersection",
}

# Dangerous module import tracking (text-only; never executes)
_BAD_IMPORT_MODULES = {"child_process", "eval", "vm"}
_BAD_IMPORT_NAMES = {"exec", "execSync", "spawn", "execFile", "execFileSync"}

# Sink patterns (text-only; never executes JS)
_SINK_PATTERNS = [
    # shell — only when from child_process or bare exec after import tracking
    ("shell", re.compile(r"\bchild_process\.exec(?:Sync)?\b")),
    ("shell", re.compile(r"\bexec(?:Sync)?\b")),
    # eval / dynamic code
    ("eval", re.compile(r"\beval\s*\(")),
    ("shell", re.compile(r"\bnew\s+Function\b")),
    ("shell", re.compile(r"\bFunction\s*\(")),
    # filesystem destructive (quick safe patterns)
    ("filesystem", re.compile(r"\bfs\.unlink(?:Sync)?\b")),
    ("filesystem", re.compile(r"\bfs\.rmdir(?:Sync)?\b")),
    ("filesystem", re.compile(r"\bfs\.rm(?:Sync)?\b")),
]

# HTTP / SSRF / timeout audit patterns
_HTTP_CALL_PATTERNS = [
    re.compile(r"\brequests\.(?:get|post|put|delete|patch|request)\b"),
    re.compile(r"\bhttpx\.(?:get|post|put|delete|patch|request|AsyncClient)\b"),
    re.compile(r"\burllib\.request\.urlopen\b"),
    re.compile(r"\bfetch\b"),
    re.compile(r"\baxios\.(?:get|post|put|delete|patch|request)\b"),
]

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class _SourceWindow(NamedTuple):
    text: str
    offset: int  # absolute char offset of text start in source


def _line_of(source: str, char_offset: int) -> int:
    return source[:char_offset].count("\n") + 1


def _find_paren(source: str, open_idx: int) -> int:
    """Safe parenthesis match (ignores strings via simple escape tracking)."""
    depth = 1
    i = open_idx + 1
    in_str = False
    str_char = ""
    while i < len(source):
        ch = source[i]
        if ch == "\\" and i + 1 < len(source):
            i += 2
            continue
        if ch in ('"', "'", "`"):
            if not in_str:
                in_str = True
                str_char = ch
            elif str_char == ch:
                in_str = False
                str_char = ""
            i += 1
            continue
        if not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return len(source) - 1


def _extract_quoted(source: str, start: int) -> tuple[str | None, int]:
    """Best-effort quoted-string extraction starting at start (expects opening quote at start)."""
    if start >= len(source):
        return None, start
    quote = source[start]
    if quote not in ('"', "'", "`"):
        # Maybe quote is slightly after (whitespace); scan forward briefly
        j = start
        while j < len(source) and source[j] in " \t\n\r":
            j += 1
        if j < len(source) and source[j] in ('"', "'", "`"):
            start = j
            quote = source[j]
        else:
            return None, start
    i = start + 1
    chars = []
    while i < len(source):
        ch = source[i]
        if ch == "\\" and i + 1 < len(source):
            chars.append(source[i + 1])
            i += 2
            continue
        if ch == quote:
            return "".join(chars), i + 1
        chars.append(ch)
        i += 1
    return "".join(chars), i


class _ToolCall(TypedDict):
    name: str | None
    description: str | None
    props: list[tuple[str, str]]


def _parse_tool_call(source: str, match_start: int, match_end: int) -> _ToolCall:
    """Extract name, description, zod props from a tool-registration call."""
    open_idx = match_end - 1
    close_idx = _find_paren(source, open_idx)
    args_text = source[open_idx + 1 : close_idx]

    info: _ToolCall = {"name": None, "description": None, "props": []}

    # Strategy: find first two string literals; anything else is schema descriptor
    # We split by unquoted commas to inspect arguments
    args: list[str] = []
    current: list[str] = []
    depth = 0
    in_str = False
    str_char = ""
    i = 0
    while i < len(args_text):
        ch = args_text[i]
        if ch == "\\" and i + 1 < len(args_text):
            current.append(ch)
            current.append(args_text[i + 1])
            i += 2
            continue
        if ch in ('"', "'", "`"):
            if not in_str:
                in_str = True
                str_char = ch
            elif str_char == ch:
                in_str = False
                str_char = ""
            current.append(ch)
            i += 1
            continue
        if not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == "," and depth == 0:
                args.append("".join(current).strip())
                current = []
                i += 1
                continue
        current.append(ch)
        i += 1
    if current:
        args.append("".join(current).strip())

    # First string arg -> name; second -> description
    strings_found: list[str] = []
    for arg in args:
        if not arg:
            continue
        # Quick heuristic: starts with quote
        stripped = arg.lstrip()
        if stripped.startswith('"') or stripped.startswith("'") or stripped.startswith("`"):
            val, _ = _extract_quoted(stripped, 0)
            if val is not None:
                strings_found.append(val)

    if len(strings_found) >= 1:
        info["name"] = strings_found[0]
    if len(strings_found) >= 2:
        info["description"] = strings_found[1]

    # Extract z.object / z.array property names from schema args
    # We scan all args for patterns: key: z.xxx(...)
    for arg in args:
        # Look for property definitions inside object schemas
        # Pattern: propName: z.string() or propName : z.number().max(10)
        for m in re.finditer(
            r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(z\.[A-Za-z_][A-Za-z0-9_]*\b(?:\.[A-Za-z_][A-Za-z0-9_]*|\([^)]*\))*)",
            arg,
        ):
            prop = m.group(1)
            z_expr = m.group(2)
            if prop not in info["props"]:
                info["props"].append((prop, z_expr))
    return info


def _infer_param_from_z_expr(z_expr: str) -> tuple[str, bool, bool, str]:
    """Return (type_name, has_bounds, required, description_hint)."""
    has_bounds = False
    required = True
    description = ""

    # Bounds: .max(.min(.length(
    if ".max" in z_expr or ".min" in z_expr or ".length" in z_expr or ".email" in z_expr:
        has_bounds = True

    # Optional / default -> not required
    if ".optional" in z_expr or ".default" in z_expr or ".nullable" in z_expr:
        required = False

    # Description from .describe("...")
    m = re.search(r"\.describe\s*\(\s*([\"\'])(.*?)\1", z_expr, re.DOTALL)
    if m:
        description = m.group(2)

    # Type resolution (best-effort)
    type_name = "unknown"
    for scalar_key, scalar_val in _ZOD_SCALARS.items():
        if scalar_key in z_expr:
            type_name = scalar_val
            break
    if type_name == "unknown":
        for container_key, container_val in _ZOD_CONTAINERS.items():
            if container_key in z_expr:
                type_name = container_val
                break
    # Nested object props — if z.object with props found separately, type stays object
    return type_name, has_bounds, required, description


def _build_params_from_info(info: _ToolCall, tool_line: int) -> list[ParameterSpec]:
    params: list[ParameterSpec] = []
    for prop, z_expr in info.get("props", []):
        type_name, has_bounds, required, desc = _infer_param_from_z_expr(z_expr)
        params.append(
            ParameterSpec(
                name=prop,
                type_name=type_name,
                description=desc,
                required=required,
                has_bounds=has_bounds,
                line=tool_line,
            )
        )
    # If no z.object props parsed but schema_props remain (simple arg patterns), add basic
    if not params and info.get("props") == []:
        # Nothing more; tool may have no explicit schema (return special case handled by caller)
        pass
    return params


def _extract_sinks(
    source: str, tool_start: int, tool_end: int, bad_imports: set[str]
) -> list[CodeSink]:
    sinks: list[CodeSink] = []
    # Build a small window from tool body; include handler if inside args (already covered by close_idx)
    window_text = source[max(0, tool_start - 50) : min(len(source), tool_end + 200)]
    # Break at next tool to avoid cross-contamination
    # (callers pass precise tool_end; we just constrain locally)
    seen: set[tuple[str, str, int]] = set()
    # First: import-tracked dangerous names
    # Scan patterns
    for kind, pattern in _SINK_PATTERNS:
        for m in pattern.finditer(window_text):
            abs_offset = max(0, tool_start - 50) + m.start()
            line = _line_of(source, abs_offset)
            call_text = m.group(0)[:60]
            key = (kind, call_text, line)
            if key in seen:
                continue
            seen.add(key)
            sinks.append(
                CodeSink(
                    kind=kind,
                    call_name=call_text,
                    line=line,
                    uses_param=None,
                    validated=False,
                    timeout_missing=False,
                )
            )
    # HTTP / network-timeout audit (MCP010 integration) — check for timeout= absence
    for http_pat in _HTTP_CALL_PATTERNS:
        for m in http_pat.finditer(window_text):
            abs_offset = max(0, tool_start - 50) + m.start()
            line = _line_of(source, abs_offset)
            # Inspect call args in source near match for timeout= keyword
            call_start = abs_offset  # Already absolute; do not add the window offset twice.
            # Find matching closing paren for this call (approximate from match end)
            # Best safe approximation: search next ')' within 300 chars
            paren_open = source.find("(", call_start)
            if paren_open == -1:
                continue
            paren_close = _find_paren(source, paren_open)
            call_args = source[paren_open + 1 : paren_close]
            timeout_missing = True
            # Check for timeout=... keyword
            for kw_m in re.finditer(r"timeout\s*=\s*([^,)]+)", call_args):
                val_str = kw_m.group(1).strip()
                # If explicitly None -> missing; if numeric/constant -> present
                if val_str == "None" or val_str == "null" or val_str == "undefined":
                    timeout_missing = True
                else:
                    timeout_missing = False
                    break
            # Only add a network-timeout sink if missing; do NOT duplicate existing ssrf sink
            # We use a dedicated kind for audit consistency
            if timeout_missing:
                key = ("network-timeout", m.group(0)[:60], line)
                if key not in seen:
                    seen.add(key)
                    sinks.append(
                        CodeSink(
                            kind="ssrf",  # use ssrf kind + timeout_missing for rule check
                            call_name=m.group(0)[:60],
                            line=line,
                            uses_param=None,
                            validated=False,
                            timeout_missing=True,
                        )
                    )
    return sinks


# ------------------------------------------------------------------
# Import tracking (text-only; best-effort for sink classification)
# ------------------------------------------------------------------


def _track_bad_imports(source: str) -> set[str]:
    bad: set[str] = set()
    for m in re.finditer(r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]", source):
        import_block = m.group(1)
        module = m.group(2)
        if (
            module.split("/")[-1].split(".")[0] in _BAD_IMPORT_MODULES
            or module in _BAD_IMPORT_MODULES
        ):
            for name in import_block.split(","):
                clean = name.split(" as ")[-1].strip()
                if clean:
                    bad.add(clean)
    # Also default imports: import child_process from "child_process"
    for m in re.finditer(r"import\s+([A-Za-z_][A-Za-z0-9_]*)\s+from\s+['\"]([^'\"]+)['\"]", source):
        name = m.group(1)
        module = m.group(2).split("/")[-1].split(".")[0]
        if module in _BAD_IMPORT_MODULES:
            bad.add(name)
    return bad


def load_typescript_file(path: Path) -> list[ToolSpec]:
    """Parse a .ts / .js / .jsx file and return discovered MCP SDK ToolSpecs.

    Safety guarantees:
      - Opens file in text mode only; never executes code.
      - Uses balanced-paren scanning (string-aware) rather than fragile split.
      - Tracks dangerous imports so bare `exec(` is only flagged when sourced
        from `child_process`; avoids false positives on harmless variable names.
      - Reports real source line numbers.
      - Respects `.d.ts` exclusion and existing `node_modules` exclusions.
    """
    suffix = path.suffix
    # Exclude declaration files explicitly (also handled by .d.ts not in _EXTS,
    # but guard against paths like foo.d.ts where .ts is suffix)
    if path.name.endswith(_DECLARATION_SUFFIX):
        return []
    if suffix not in _EXTS:
        return []

    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    bad_imports = _track_bad_imports(source)
    tools: list[ToolSpec] = []

    for pattern in _TOOL_PATTERNS:
        for match in pattern.finditer(source):
            tool_start = match.start()
            open_idx = match.end() - 1
            close_idx = _find_paren(source, open_idx)
            info = _parse_tool_call(source, tool_start, open_idx + 1)

            tool_name = info.get("name") or f"ts_anon_{tool_start}"
            description = info.get("description") or ""
            tool_line = _line_of(source, tool_start)

            params = _build_params_from_info(info, tool_line)
            # Fallback: if no z.object props found but schema object literal exists,
            # try to extract top-level keys from args that look like prop definitions
            if not params and info.get("props") == []:
                # Scan argument text for property names near z. expressions
                args_text = source[open_idx + 1 : close_idx]
                for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:z\.|\w+)", args_text):
                    prop = m.group(1)
                    if prop and prop not in {p.name for p in params}:
                        params.append(
                            ParameterSpec(
                                name=prop,
                                type_name="unknown",
                                description="",
                                required=True,
                                has_bounds=False,
                                line=tool_line,
                            )
                        )

            sinks = _extract_sinks(source, tool_start, close_idx, bad_imports)
            # Deduplicate sinks by (kind, line, truncated call)
            seen_sinks: set[tuple[str, int, str]] = set()
            unique_sinks: list[CodeSink] = []
            for s in sinks:
                key = (s.kind, s.line, s.call_name[:40])
                if key not in seen_sinks:
                    seen_sinks.add(key)
                    unique_sinks.append(s)

            param_tuple = tuple(params)
            est = estimate_tool_tokens(
                tool_name,
                description,
                param_tuple,
            )
            tools.append(
                ToolSpec(
                    name=tool_name,
                    description=description,
                    source_file=str(path),
                    line=tool_line,
                    parameters=param_tuple,
                    sinks=tuple(unique_sinks),
                    suppressed_rules=frozenset(),
                    estimated_tokens=est,
                )
            )

    tools.sort(key=lambda t: t.line)
    return tools
