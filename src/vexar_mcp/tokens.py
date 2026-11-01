"""Fast, deterministic token estimation for MCP tool schemas and descriptions."""

from __future__ import annotations

import json
import math
import re

from vexar_mcp.models import ParameterSpec

_TOKEN_CHUNK_RE = re.compile(r"[A-Za-z0-9]+|[^\sA-Za-z0-9]")


def estimate_tokens(text: str) -> int:
    """Estimate BPE token count for arbitrary English/JSON/code text without network dependencies.

    Splits words and punctuation and accounts for subword splitting on long identifiers.
    This is a heuristic, not an accuracy bound. Measurements against cl100k_base
    and o200k_base on the original synthetic corpus are in benchmarks/token_report.json;
    reproduce with scripts/benchmark_tokens.py and the optional exact-tokenizer extra.
    Serialization is measured separately from model framing; the +8 addend below
    is not calibrated by that benchmark. See docs/usage.md for scope and limitations.
    """
    if not text or not text.strip():
        return 0
    chunks = _TOKEN_CHUNK_RE.findall(text)
    total = 0.0
    for chunk in chunks:
        if len(chunk) <= 4:
            total += 1.0
        else:
            total += math.ceil(len(chunk) / 4.2)
    return max(1, round(total))


def estimate_tool_tokens(
    name: str,
    description: str,
    parameters: tuple[ParameterSpec, ...],
    raw_schema: dict[str, object] | None = None,
) -> int:
    """Estimate total context-window token footprint of a single tool definition."""
    if raw_schema:
        serialized = json.dumps(
            {"name": name, "description": description, "inputSchema": raw_schema},
            separators=(",", ":"),
        )
        return estimate_tokens(serialized) + 8

    # Base JSON framing overhead per tool in system prompt (`name`, `description`, `inputSchema`)
    tokens = 12 + estimate_tokens(name) + estimate_tokens(description)
    for param in parameters:
        tokens += 6 + estimate_tokens(param.name)
        if param.type_name:
            tokens += estimate_tokens(param.type_name)
        if param.description:
            tokens += estimate_tokens(param.description)
    return tokens
