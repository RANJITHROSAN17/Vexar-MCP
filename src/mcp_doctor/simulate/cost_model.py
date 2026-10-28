"""Projection of MCP tool-catalog token cost into per-call and per-month spend.

Two rules make this trustworthy rather than decorative:

1. **One estimator.** Token counts come from :mod:`mcp_doctor.tokens`, the same code that
   drives ``MCP002`` and ``MCP014``. A simulation must never disagree with a finding it is
   supposedly explaining, so this module does not carry a second estimator.
2. **Prices are an explicit, dated input.** An unknown model raises instead of falling back
   to a default price. A projection built on a silently substituted price is a guess wearing
   a dollar sign, which is the failure mode this project exists to remove.

The output is a *projection*: per-turn catalog tax, spend over a call volume you choose, and
the marginal cost of the tools you could trim. It is not a bill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp_doctor.models import ScanReport

#: Input-token price in USD per 1M tokens. Tool catalogs are sent as input on every turn.
#: Reviewed 2026-10; treat as a configurable knob, not a contract. Override via
#: ``--input-price`` when your contract differs.
MODEL_PRICES_USD_PER_MTOK: dict[str, float] = {
    "claude-sonnet-4.5": 3.00,
    "claude-haiku-4.5": 1.00,
    "claude-opus-4.1": 15.00,
    "gpt-4.1": 2.00,
    "gpt-4.1-mini": 0.40,
    "gpt-5": 1.25,
    "gemini-2.5-pro": 1.25,
    "gemini-2.5-flash": 0.30,
}

PRICING_AS_OF = "2026-10"

#: Assumed cache/prefix discount is *not* applied: a pessimistic projection is the useful one.
DEFAULT_CALLS_PER_DAY = 1000


class UnknownModelError(ValueError):
    """Raised when a model is not in the reviewed price table."""

    def __init__(self, model: str) -> None:
        known = ", ".join(sorted(MODEL_PRICES_USD_PER_MTOK))
        super().__init__(
            f"unknown model '{model}': no reviewed price on file. "
            f"Known models: {known}. "
            f"Pass --input-price to project with your own contract rate."
        )
        self.model = model


@dataclass(frozen=True)
class ToolCost:
    """Projected cost contribution of a single tool."""

    name: str
    tokens: int
    source_file: str
    line: int

    def cost_per_call(self, price_per_mtok: float) -> float:
        """Return this tool's contribution to a single call."""
        return self.tokens / 1_000_000 * price_per_mtok

    def monthly_usd(self, price_per_mtok: float, calls_per_day: int) -> float:
        """Return this tool's monthly contribution at the given volume."""
        return per_month_usd(self.tokens, price_per_mtok, calls_per_day)


@dataclass(frozen=True)
class CostProjection:
    """Result of projecting a scan onto a model, a volume and a price."""

    model: str
    price_per_mtok: float
    calls_per_day: int
    catalog_tokens: int
    tools: tuple[ToolCost, ...]
    tools_scanned: int
    analysis_complete: bool = True
    notes: tuple[str, ...] = field(default=())

    @property
    def cost_per_call_usd(self) -> float:
        """Catalog tax paid on every single call."""
        return self.catalog_tokens / 1_000_000 * self.price_per_mtok

    @property
    def monthly_usd(self) -> float:
        """Catalog tax over 30 days at the configured volume."""
        return per_month_usd(self.catalog_tokens, self.price_per_mtok, self.calls_per_day)

    def top(self, limit: int = 5) -> tuple[ToolCost, ...]:
        """Return the most expensive tools, highest first."""
        return tuple(sorted(self.tools, key=lambda t: t.tokens, reverse=True)[:limit])

    def trim_savings(self, count: int) -> float:
        """Monthly saving from removing the `count` most expensive tools."""
        trimmed = sum(t.tokens for t in self.top(count))
        return per_month_usd(trimmed, self.price_per_mtok, self.calls_per_day)

    def as_dict(self) -> dict[str, Any]:
        """Machine-readable projection for CI and dashboards."""
        return {
            "model": self.model,
            "pricing_as_of": PRICING_AS_OF,
            "input_price_usd_per_mtok": self.price_per_mtok,
            "calls_per_day": self.calls_per_day,
            "catalog_tokens_per_turn": self.catalog_tokens,
            "tools_scanned": self.tools_scanned,
            "cost_per_call_usd": round(self.cost_per_call_usd, 6),
            "monthly_usd": round(self.monthly_usd, 2),
            "analysis_complete": self.analysis_complete,
            "top_tools": [
                {
                    "name": t.name,
                    "tokens": t.tokens,
                    "source": f"{t.source_file}:{t.line}",
                    "monthly_usd": round(t.monthly_usd(self.price_per_mtok, self.calls_per_day), 2),
                }
                for t in self.top()
            ],
            "notes": list(self.notes),
        }


def per_month_usd(tokens: int, price_per_mtok: float, calls_per_day: int) -> float:
    """Monthly spend for `tokens` of catalog sent on every call."""
    return tokens / 1_000_000 * price_per_mtok * calls_per_day * 30


def resolve_price(model: str, input_price: float | None = None) -> tuple[str, float]:
    """Return `(model_label, price_per_mtok)`, rejecting unknown models without a price."""
    if input_price is not None:
        if input_price < 0:
            raise ValueError("--input-price must be >= 0")
        return model, input_price
    if model not in MODEL_PRICES_USD_PER_MTOK:
        raise UnknownModelError(model)
    return model, MODEL_PRICES_USD_PER_MTOK[model]


def simulate(
    report: ScanReport,
    *,
    model: str = "claude-sonnet-4.5",
    calls_per_day: int = DEFAULT_CALLS_PER_DAY,
    input_price: float | None = None,
) -> CostProjection:
    """Project the catalog cost of an already-completed scan.

    Takes a :class:`ScanReport` rather than raw text so the projection cannot drift from the
    token counts the rules reported.
    """
    if calls_per_day < 0:
        raise ValueError("--calls-per-day must be >= 0")
    label, price = resolve_price(model, input_price)

    tools = tuple(
        ToolCost(
            name=tool.name,
            tokens=tool.estimated_tokens,
            source_file=tool.source_file,
            line=tool.line,
        )
        for tool in report.tools
    )

    notes: list[str] = []
    if not report.is_complete:
        notes.append(
            "Projection is a LOWER BOUND: not every file was analyzed, so the real "
            "catalog may be larger than this."
        )
    if not tools:
        notes.append("No tools were found, so there is nothing to project.")

    return CostProjection(
        model=label,
        price_per_mtok=price,
        calls_per_day=calls_per_day,
        catalog_tokens=report.summary.total_estimated_tokens,
        tools=tools,
        tools_scanned=len(tools),
        analysis_complete=report.is_complete,
        notes=tuple(notes),
    )


def render_projection(projection: CostProjection) -> str:
    """Render a human-readable projection for the terminal."""
    calls = f"{projection.calls_per_day:,}"
    lines = [
        f"MODEL: {projection.model}  (input ${projection.price_per_mtok:.2f}/1M tokens, "
        f"priced {PRICING_AS_OF})",
        f"VOLUME: {calls} calls/day (~{projection.calls_per_day * 30:,} calls/month)",
        "",
        f"{'TOOL NAME':<32} {'TOKENS':>8} {'USD/CALL':>12} {'USD/MONTH':>12}  SOURCE",
        "-" * 84,
    ]
    for tool in projection.top(limit=10):
        lines.append(
            f"{tool.name[:32]:<32} {tool.tokens:>8} {tool.cost_per_call(projection.price_per_mtok):>12.6f} "
            f"{tool.monthly_usd(projection.price_per_mtok, projection.calls_per_day):>12.2f}  "
            f"{tool.source_file}:{tool.line}"
        )
    lines.append("-" * 84)
    lines.append(
        f"{'CATALOG TOTAL':<32} {projection.catalog_tokens:>8} "
        f"{projection.cost_per_call_usd:>12.6f} {projection.monthly_usd:>12.2f}"
    )
    lines.append("")
    lines.append(
        f"Every call pays the catalog tax: {projection.catalog_tokens:,} tokens "
        f"x {calls} calls/day = ${projection.monthly_usd:,.2f}/month."
    )
    for count, label in ((1, "costliest tool"), (3, "costliest 3 tools")):
        # Only meaningful when tools remain after the trim, otherwise it just restates the total.
        if projection.tools_scanned > count:
            lines.append(
                f"  Removing the {label} would save ${projection.trim_savings(count):,.2f}/month."
            )
    for note in projection.notes:
        lines.append("")
        lines.append(f"WARNING: {note}")
    return "\n".join(lines)
