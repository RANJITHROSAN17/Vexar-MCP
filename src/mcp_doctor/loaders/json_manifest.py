"""JSON tool manifest loader for MCP `tools/list`, OpenAI, and Anthropic tool schemas."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mcp_doctor.models import IssueKind, ParameterSpec, ParseIssue, ToolSpec
from mcp_doctor.tokens import estimate_tool_tokens

_BOUND_KEYS = frozenset(
    {
        "maximum",
        "exclusiveMaximum",
        "maxItems",
        "maxLength",
        "enum",
        "const",
    }
)


def _parse_schema_params(schema: dict[str, Any], line: int) -> tuple[ParameterSpec, ...]:
    """Normalize a JSON-Schema ``properties`` block into parameters.

    Every parameter inherits the tool's line. A property's own line is not recoverable
    from a decoded document, and reporting the tool's line keeps a finding on the same
    location as every other finding about that tool instead of pointing at line 1.
    """
    props = schema.get("properties")
    if not isinstance(props, dict):
        return ()
    req_raw = schema.get("required", [])
    required_set = {str(x) for x in req_raw} if isinstance(req_raw, list) else set()

    params: list[ParameterSpec] = []
    for name, prop in props.items():
        if not isinstance(prop, dict):
            params.append(
                ParameterSpec(name=str(name), type_name=None, required=name in required_set)
            )
            continue
        raw_type = prop.get("type")
        if isinstance(raw_type, list):
            type_name: str | None = " | ".join(str(t) for t in raw_type)
        elif isinstance(raw_type, str):
            type_name = raw_type
        elif "anyOf" in prop or "oneOf" in prop:
            variants = prop.get("anyOf") or prop.get("oneOf") or []
            types = [
                str(v.get("type"))
                for v in variants
                if isinstance(v, dict) and v.get("type") is not None
            ]
            type_name = " | ".join(types) if types else None
        else:
            type_name = None

        has_bounds = any(k in prop for k in _BOUND_KEYS)
        desc = str(prop.get("description", ""))
        params.append(
            ParameterSpec(
                name=str(name),
                type_name=type_name,
                description=desc,
                required=str(name) in required_set,
                has_bounds=has_bounds,
                line=line,
            )
        )
    return tuple(params)


def _normalize_tool_entry(
    entry: Any, source_file: str, index: int, line: int | None = None
) -> ToolSpec | None:
    if not isinstance(entry, dict):
        return None

    # OpenAI chat-completions tool wrapper: {"type": "function", "function": {...}}
    if entry.get("type") == "function" and isinstance(entry.get("function"), dict):
        entry = entry["function"]

    name = entry.get("name")
    if not isinstance(name, str):
        return None

    schema = entry.get("inputSchema") or entry.get("input_schema") or entry.get("parameters")
    if not isinstance(schema, dict):
        # Not an MCP / LLM tool manifest entry (avoid false positives on package.json, tsconfig.json, etc.)
        return None

    description = str(entry.get("description", ""))
    # JSON has no cheap way to report the real line, so the entry's position in the array
    # stands in for it. YAML can report the true line and passes one in.
    resolved_line = line if line is not None else index + 1
    params = _parse_schema_params(schema, resolved_line)
    est_tokens = estimate_tool_tokens(name, description, params, raw_schema=schema)

    return ToolSpec(
        name=name,
        description=description,
        source_file=source_file,
        line=resolved_line,
        parameters=params,
        raw_schema=schema,
        estimated_tokens=est_tokens,
    )


def load_json_manifest(path: Path, *, issues: list[ParseIssue] | None = None) -> list[ToolSpec]:
    """Load MCP / OpenAI / Anthropic tool specifications from a JSON file.

    Args:
        path: JSON manifest to read.
        issues: Optional sink for analysis failures. Malformed JSON or an unreadable file
            appends a :class:`ParseIssue` instead of silently returning no tools - a
            manifest that cannot be parsed is an analysis failure, not a clean result.
    """
    try:
        text = path.read_text(encoding="utf-8")
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
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        if issues is not None:
            issues.append(
                ParseIssue(
                    source_file=str(path),
                    kind=IssueKind.INVALID_JSON,
                    message=f"invalid JSON: {exc.msg}",
                    line=exc.lineno,
                )
            )
        return []

    return parse_tool_entries(extract_tool_candidates(raw), str(path))


def extract_tool_candidates(raw: Any) -> list[Any]:
    """Return the list of tool entries from an already-decoded manifest document.

    Handles the four container shapes a tool manifest can take: a ``tools`` list, an
    MCP ``result.tools`` envelope, a single tool object, and a bare list of tools.
    Anything else yields no candidates, which is how unrelated files (``package.json``,
    ``tsconfig.json``, a CI workflow) are ignored instead of reported as empty manifests.

    Shared with the YAML loader so both formats accept exactly the same shapes.
    """
    if isinstance(raw, dict):
        tools = raw.get("tools")
        if isinstance(tools, list):
            return [*tools]
        result = raw.get("result")
        if isinstance(result, dict):
            nested = result.get("tools")
            if isinstance(nested, list):
                return [*nested]
        if "name" in raw and any(k in raw for k in ("inputSchema", "input_schema", "parameters")):
            return [raw]
        return []
    if isinstance(raw, list):
        return list(raw)
    return []


def parse_tool_entries(
    candidates: Sequence[Any], source_file: str, *, lines: Sequence[int | None] | None = None
) -> list[ToolSpec]:
    """Normalize already-decoded tool entries into ToolSpec objects.

    Shared by the JSON and YAML file loaders and the live MCP introspection path, so a
    tool fetched from a running server is analysed by exactly the same code as one read
    from disk.

    Args:
        candidates: Decoded tool entries.
        source_file: Path recorded on every :class:`ToolSpec`.
        lines: Optional 1-based source line per entry, for formats that can report one.
            Omitted by JSON callers, which fall back to the entry's array position.
    """
    tools: list[ToolSpec] = []
    for idx, item in enumerate(candidates):
        spec = _normalize_tool_entry(
            item,
            source_file,
            idx,
            lines[idx] if lines is not None and idx < len(lines) else None,
        )
        if spec is not None:
            tools.append(spec)
    return tools
