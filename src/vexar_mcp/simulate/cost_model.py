"""Projection of MCP tool-catalog token cost into per-call and per-month spend.

Two rules make this trustworthy rather than decorative:

1. **One estimator.** Token counts come from :mod:`vexar_mcp.tokens`, the same code that
   drives ``MCP002`` and ``MCP014``. A simulation must never disagree with a finding it is
   supposedly explaining, so this module does not carry a second estimator.
2. **Prices are an explicit, dated input.** An unknown model raises instead of falling back
   to a default price. A projection built on a silently substituted price is a guess wearing
   a dollar sign, which is the failure mode this project exists to remove.
3. **Every price carries the date a human last checked it.** The table lives in a data file
   (:data:`PRICING_MANIFEST`) rather than in this module, so updating a rate is a reviewable
   one-line data change instead of an edit to logic. Entries older than
   :data:`REVIEW_WINDOW_DAYS` fail the test suite and make ``simulate`` say so in its output:
   a projection cannot quietly quote a rate that has aged out.

The output is a *projection*: per-turn catalog tax, spend over a call volume you choose, and
the marginal cost of the tools you could trim. It is not a bill.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from typing import Any

from vexar_mcp.models import ScanReport

#: How long a price may go unreviewed before the table is considered stale.
#:
#: This is a *policy* number, so it lives in code and is changed by review, not by editing the
#: data file. Six months is already generous for a list price that vendors have repriced several
#: times a year; tightening it is the cheaper mistake.
REVIEW_WINDOW_DAYS = 180

#: Name of the reviewed price manifest shipped inside this package.
PRICING_MANIFEST = "pricing.json"

#: Assumed cache/prefix discount is *not* applied: a pessimistic projection is the useful one.
DEFAULT_CALLS_PER_DAY = 1000


class PricingError(ValueError):
    """Raised when the shipped price manifest is missing, malformed, or self-contradictory."""


@dataclass(frozen=True)
class PriceEntry:
    """One model's input price and the date a human last checked it."""

    model: str
    input_usd_per_mtok: float
    reviewed_on: date

    def age_days(self, today: date) -> int:
        """Days since this entry was last reviewed; never negative, so clock skew reads as fresh."""
        return max(0, (today - self.reviewed_on).days)


@dataclass(frozen=True)
class PricingTable:
    """A dated, reviewable set of model prices loaded from the shipped manifest."""

    entries: Mapping[str, PriceEntry]
    source: str = PRICING_MANIFEST

    def __contains__(self, model: object) -> bool:
        return model in self.entries

    @property
    def prices(self) -> dict[str, float]:
        """Model name to input price, in USD per 1M tokens."""
        return {name: entry.input_usd_per_mtok for name, entry in self.entries.items()}

    def price(self, model: str) -> float:
        """Return the reviewed price for `model`, raising if it is not on file."""
        return self.entries[model].input_usd_per_mtok

    @property
    def as_of(self) -> str:
        """The oldest review date in the table, as ``YYYY-MM``.

        The *oldest* entry, not the newest: one number that reads "you can rely on this table
        from here on" has to describe the weakest thing in it.
        """
        return min(entry.reviewed_on for entry in self.entries.values()).strftime("%Y-%m")

    def stale(self, today: date, window_days: int = REVIEW_WINDOW_DAYS) -> tuple[PriceEntry, ...]:
        """Entries whose review is older than `window_days`, oldest first."""
        overdue = (e for e in self.entries.values() if e.age_days(today) > window_days)
        return tuple(sorted(overdue, key=lambda e: e.reviewed_on))


def _parse_entry(model: str, raw: object, source: str) -> PriceEntry:
    """Validate one manifest entry, refusing anything a projection could not honestly use."""
    if not isinstance(raw, dict):
        raise PricingError(f"{source}: entry for '{model}' must be an object")

    price = raw.get("input_usd_per_mtok")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise PricingError(f"{source}: '{model}' needs a numeric input_usd_per_mtok")
    if price <= 0:
        raise PricingError(f"{source}: '{model}' has a non-positive price ({price})")

    reviewed = raw.get("reviewed_on")
    if not isinstance(reviewed, str):
        raise PricingError(f"{source}: '{model}' needs a reviewed_on date (YYYY-MM-DD)")
    try:
        reviewed_on = date.fromisoformat(reviewed)
    except ValueError as exc:
        raise PricingError(
            f"{source}: '{model}' has reviewed_on '{reviewed}', which is not an ISO date"
        ) from exc

    return PriceEntry(model=model, input_usd_per_mtok=float(price), reviewed_on=reviewed_on)


def parse_pricing_table(document: object, source: str = PRICING_MANIFEST) -> PricingTable:
    """Build a :class:`PricingTable` from an already-parsed manifest document."""
    if not isinstance(document, dict):
        raise PricingError(f"{source}: manifest must be a JSON object")
    models = document.get("models")
    if not isinstance(models, dict) or not models:
        raise PricingError(f"{source}: manifest has no 'models' object")

    entries: dict[str, PriceEntry] = {}
    for model, raw in models.items():
        if not isinstance(model, str):
            raise PricingError(f"{source}: model names must be strings, got {model!r}")
        entries[model] = _parse_entry(model, raw, source)
    return PricingTable(entries=entries, source=source)


def load_pricing_table(path: Any = None) -> PricingTable:
    """Load the shipped price manifest, or the one at `path` when given.

    Reading through :mod:`importlib.resources` rather than ``__file__`` keeps this working from
    a wheel, a zip import, or an editable install.
    """
    if path is not None:
        return parse_pricing_table(json.loads(path.read_text(encoding="utf-8")), str(path))
    manifest = resources.files(__package__ or "vexar_mcp.simulate").joinpath(PRICING_MANIFEST)
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - a broken or incomplete install
        raise PricingError(
            f"price manifest '{PRICING_MANIFEST}' is missing from the installed package. "
            "This usually means it was not declared in [tool.setuptools.package-data]."
        ) from exc
    return parse_pricing_table(document, PRICING_MANIFEST)


#: The reviewed table this build ships with.
PRICING_TABLE = load_pricing_table()

#: Input-token price in USD per 1M tokens, keyed by model. Tool catalogs are sent as input on
#: every turn. Derived from :data:`PRICING_TABLE`; treat as a configurable knob, not a contract.
#: Override via ``--input-price`` when your contract differs.
MODEL_PRICES_USD_PER_MTOK: dict[str, float] = PRICING_TABLE.prices

#: Oldest review date in the shipped table, as ``YYYY-MM``.
PRICING_AS_OF = PRICING_TABLE.as_of


class UnknownModelError(ValueError):
    """Raised when a model is not in the reviewed price table."""

    def __init__(self, model: str) -> None:
        known = ", ".join(sorted(PRICING_TABLE.entries))
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
    #: True when the shipped price table contains an entry past its review window. The
    #: projection is still computed — refusing to print a number would not make prices fresher —
    #: but it is labelled so nobody screenshots a stale rate as current.
    pricing_stale: bool = False

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
            "pricing_stale": self.pricing_stale,
            "pricing_review_window_days": REVIEW_WINDOW_DAYS,
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
    if model not in PRICING_TABLE:
        raise UnknownModelError(model)
    return model, PRICING_TABLE.price(model)


def _stale_price_note(label: str, table: PricingTable, today: date) -> tuple[str | None, bool]:
    """Return the staleness note for this run, plus whether the table is past its window.

    The model actually being priced gets its own sentence when it is overdue: "the table is old"
    is background, "the rate you are reading right now is old" is the thing worth acting on.
    """
    overdue = table.stale(today)
    if not overdue:
        return None, False

    oldest = overdue[0]
    used = table.entries[label] if label in table else None
    if used is not None and used.age_days(today) > REVIEW_WINDOW_DAYS:
        return (
            f"The price for {label} was last reviewed on {used.reviewed_on.isoformat()} "
            f"({used.age_days(today)} days ago, review window {REVIEW_WINDOW_DAYS}). "
            "Re-check the vendor's published input price before relying on this figure, "
            f"then update reviewed_on in {table.source}."
        ), True
    return (
        f"{len(overdue)} of {len(table.entries)} prices in the table are past their "
        f"{REVIEW_WINDOW_DAYS}-day review window (oldest {oldest.model}, "
        f"{oldest.reviewed_on.isoformat()}). The {label} rate used here is still within it, "
        f"but the table as a whole needs a review pass in {table.source}."
    ), True


def simulate(
    report: ScanReport,
    *,
    model: str = "claude-sonnet-4.5",
    calls_per_day: int = DEFAULT_CALLS_PER_DAY,
    input_price: float | None = None,
    today: date | None = None,
    pricing: PricingTable | None = None,
) -> CostProjection:
    """Project the catalog cost of an already-completed scan.

    Takes a :class:`ScanReport` rather than raw text so the projection cannot drift from the
    token counts the rules reported.

    ``today`` and ``pricing`` exist so the review-window logic can be tested against a fixed
    date and a synthetic table instead of waiting for the real one to age, or editing it.
    """
    if calls_per_day < 0:
        raise ValueError("--calls-per-day must be >= 0")
    label, price = resolve_price(model, input_price)
    table = pricing or PRICING_TABLE
    checked_on = today or date.today()

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
    pricing_stale = False
    if input_price is None:
        # A caller-supplied rate is not in the table, so the table's age says nothing about
        # whether *this* number is right. Warning anyway would be noise on a correct input.
        note, pricing_stale = _stale_price_note(label, table, checked_on)
        if note is not None:
            notes.append(note)
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
        pricing_stale=pricing_stale,
    )


def render_projection(projection: CostProjection) -> str:
    """Render a human-readable projection for the terminal."""
    calls = f"{projection.calls_per_day:,}"
    # The header is the line people screenshot, so the review state belongs in it and not only
    # in the trailing notes. When the table is fresh the words are unchanged.
    priced = PRICING_AS_OF if not projection.pricing_stale else f"{PRICING_AS_OF}, REVIEW OVERDUE"
    lines = [
        f"MODEL: {projection.model}  (input ${projection.price_per_mtok:.2f}/1M tokens, "
        f"priced {priced})",
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
