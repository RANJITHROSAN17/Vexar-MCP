"""Token-cost benchmark simulator for MCP tool schemas."""

from collections.abc import Sequence
from typing import Any

# Approximate pricing (per 1K tokens) — configurable
PRICE_MAP = {"claude-3-5-sonnet": 3.00, "gpt-4-turbo": 10.00, "default": 5.00}


def estimate_tokens(schema_description: str, params_json: str) -> int:
    """Rough token estimate from description length + schema complexity."""
    # Approximate: 1 token ~ 4 chars for English; schema overhead ~20% of char count
    desc_tokens = max(len(schema_description) // 4, 10)
    params_tokens = max(len(params_json) // 3, 5)
    return int(desc_tokens + params_tokens)


def simulate_cost(
    tools: Sequence[Any], model: str = "claude-3-5-sonnet"
) -> dict[str, str | int | float]:
    """Estimate schema token cost for a sequence of tool mappings."""
    price = PRICE_MAP.get(model, PRICE_MAP["default"])
    total_tokens = sum(
        estimate_tokens(str(t.get("description", "")), str(t.get("parameters", "{}")))
        for t in tools
    )
    per_1k = (total_tokens / 1000) * price
    return {
        "model": model,
        "total_tokens": total_tokens,
        "cost_per_1000_calls_usd": round(per_1k, 4),
        "note": "Estimates based on schema size; actual LLM pricing varies by provider.",
    }
