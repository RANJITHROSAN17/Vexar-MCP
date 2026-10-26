"""Static TypeScript / JavaScript loader for MCP SDK (server.tool / registerTool).

Safe by design: pure text parsing (re / string scanning). No Node runtime,
no eval(), no subprocess, no file execution. Only reads .ts / .js source.
"""

from __future__ import annotations

import re
from pathlib import Path

from mcp_doctor.models import CodeSink, ParameterSpec, ToolSpec
from mcp_doctor.tokens import estimate_tool_tokens

# Tool-registration patterns (safe: only match source text, never execute)
_TOOL_RE = re.compile(
    r"(?:\bserver\.)?tool\s*\(",
    re.VERBOSE,
)
_REGISTER_RE = re.compile(
    r"registerTool\s*\(",
    re.VERBOSE,
)

# Zod schema patterns for basic type extraction
_ZOD_STRING_RE = re.compile(r"z\.string\b")
_ZOD_NUMBER_RE = re.compile(r"z\.number\b")
_ZOD_BOOLEAN_RE = re.compile(r"z\.boolean\b")
_ZOD_ANY_RE = re.compile(r"z\.any\b")
_ZOD_ARRAY_RE = re.compile(r"z\.array\b")
_ZOD_OBJ_RE = re.compile(r"z\.object\b")

# Dangerous sink patterns (text-only detection; no execution)
_TS_SINK_PATTERNS = [
    ("shell", re.compile(r"\bchild_process\.exec(?:Sync)?\b")),
    ("shell", re.compile(r"\bexec(?:Sync)?\s*\(")),
    ("eval", re.compile(r"\beval\s*\(")),
    ("shell", re.compile(r"\bnew\s+Function\b")),
    ("shell", re.compile(r"\bFunction\s*\(")),
]

# Ignore declaration files and declaration-style patterns
_DECLARATION_EXT = ".d.ts"


def _extract_quoted_string(text: str, start: int) -> tuple[str | None, int]:
    """Extract a double-quoted string from text starting at start. Returns (value, new_pos)."""
    i = start
    while i < len(text) and text[i] != '"':
        i += 1
    if i >= len(text):
        return None, i
    i += 1  # skip opening quote
    quote_start = i
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            i += 2
            continue
        if ch == '"':
            return text[quote_start:i], i + 1
        i += 1
    return text[quote_start:i], i


def _find_matching_paren(text: str, open_idx: int) -> int:
    """Find index of matching closing paren for opening at open_idx."""
    depth = 1
    i = open_idx + 1
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if ch == '"' or ch == "'" or ch == "`":
            # Simple string toggle; good enough for safe static scanning
            in_string = not in_string
            i += 1
            continue
        if not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return len(text) - 1


def _parse_server_tool_args(arg_text: str) -> dict:
    """Naive but safe extraction of first-string args and object props."""
    result: dict = {"name": None, "description": None, "schema_props": []}
    # Try to find first quoted string -> name
    m = re.search(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']', arg_text)
    if m:
        result["name"] = m.group(1)
    # Second quoted string -> description (heuristic: after first comma or second quote)
    quotes = re.findall(r'["\']([^"\']{3,200})["\']', arg_text)
    if len(quotes) >= 2:
        result["description"] = quotes[1]
    elif len(quotes) == 1:
        # Only one quote: could be description if it looks long
        if len(quotes[0]) > 10:
            result["description"] = quotes[0]
    # Look for zod schema references or property names near schema arg
    # Extract property names from nearby object literal patterns (e.g., { query: z.string() })
    props = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*z\.", arg_text)
    for prop in props:
        if prop not in result["schema_props"]:
            result["schema_props"].append(prop)
    return result


def _extract_zod_params(source: str, tool_start: int, tool_end: int) -> list[ParameterSpec]:
    """Scan near tool definition for zod-typed parameter patterns."""
    window = source[max(0, tool_start - 200) : min(len(source), tool_end + 200)]
    params: list[ParameterSpec] = []
    # Extract property assignments from nearby object patterns
    # Pattern: propName: z.string() / z.number().max(...) / z.any()
    for m in re.finditer(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(z\.string\b(?:\(\))?|z\.number\b(?:\.[A-Za-z_]+(?:\([^)]*\))?)?|z\.boolean\b(?:\(\))?|z\.any\b(?:\(\))?|z\.array\b(?:\([^)]*\))?|z\.object\b(?:\([^)]*\))?)",
        window,
    ):
        prop = m.group(1)
        z_expr = m.group(2)
        # Simple type name mapping
        if "z.string" in z_expr:
            type_name = "string"
        elif "z.number" in z_expr:
            type_name = "number"
        elif "z.boolean" in z_expr:
            type_name = "boolean"
        elif "z.any" in z_expr:
            type_name = "any"
        elif "z.array" in z_expr:
            type_name = "list"
        elif "z.object" in z_expr:
            type_name = "object"
        else:
            type_name = "unknown"
        # Bound detection from .max(...), .min(...) in z_expr
        has_bounds = ".max" in z_expr or ".min" in z_expr or ".length" in z_expr
        params.append(
            ParameterSpec(
                name=prop,
                type_name=type_name,
                description="",
                required=True,
                has_bounds=has_bounds,
                line=1,
            )
        )
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[ParameterSpec] = []
    for p in params:
        if p.name not in seen:
            seen.add(p.name)
            deduped.append(p)
    return deduped


def load_typescript_file(path: Path) -> list[ToolSpec]:
    """Parse a .ts / .js file and return discovered MCP SDK ToolSpecs.

    Safety: reads text only. Never executes JavaScript/TypeScript.
    Ignores .d.ts declaration files and skips node_modules via engine exclusions.
    """
    if path.suffix == _DECLARATION_EXT or path.name.endswith(".d.ts"):
        return []
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    tools: list[ToolSpec] = []
    # Find all tool registrations
    for pattern in (_TOOL_RE, _REGISTER_RE):
        for match in pattern.finditer(source):
            open_idx = match.end() - 1  # index of opening paren
            close_idx = _find_matching_paren(source, open_idx)
            arg_text = source[open_idx + 1 : close_idx]
            info = _parse_server_tool_args(arg_text)
            tool_name = info.get("name") or f"anon_ts_{match.start()}"

            # Build parameters from nearby zod patterns
            params = _extract_zod_params(source, match.start(), close_idx)
            # If no zod params found but we have schema_props from args, add basic ones
            if not params and info.get("schema_props"):
                params = [
                    ParameterSpec(
                        name=p,
                        type_name="unknown",
                        description="",
                        required=True,
                        has_bounds=False,
                        line=1,
                    )
                    for p in info["schema_props"]
                ]

            # Extract dangerous sinks (text patterns only)
            sinks: list[CodeSink] = []
            # Search in source around the tool definition for sink patterns
            window_start = max(0, match.start() - 50)
            window_end = min(len(source), close_idx + 200)
            # Prevent cross-tool contamination: stop at next tool registration
            for next_pat in (_TOOL_RE, _REGISTER_RE):
                nxt = next_pat.search(source, close_idx + 1)
                if nxt and nxt.start() < window_end:
                    window_end = nxt.start()
            window = source[window_start:window_end]
            for kind, sink_re in _TS_SINK_PATTERNS:
                for sm in sink_re.finditer(window):
                    line = source[: window_start + sm.start()].count("\n") + 1
                    sinks.append(
                        CodeSink(
                            kind=kind,
                            call_name=sm.group(0)[:60],
                            line=line,
                            uses_param=None,
                            validated=False,
                            timeout_missing=False,
                        )
                    )
            # Deduplicate sinks by (kind, call_name, line) roughly
            seen_sinks: set[tuple[str, str, int]] = set()
            unique_sinks: list[CodeSink] = []
            for s in sinks:
                key = (s.kind, s.call_name, s.line)
                if key not in seen_sinks:
                    seen_sinks.add(key)
                    unique_sinks.append(s)

            param_tuple = tuple(params)
            est = estimate_tool_tokens(
                tool_name,
                info.get("description") or "",
                param_tuple,
            )
            tools.append(
                ToolSpec(
                    name=tool_name,
                    description=info.get("description") or "",
                    source_file=str(path),
                    line=source[:match.start()].count("\n") + 1,
                    parameters=param_tuple,
                    sinks=tuple(unique_sinks),
                    suppressed_rules=frozenset(),
                    estimated_tokens=est,
                )
            )
    # Sort and return
    tools.sort(key=lambda t: t.line)
    return tools
