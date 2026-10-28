"""Deterministic risk scoring for scan findings.

Severity alone is a poor prioritisation signal: a `warning` for a tainted SSRF sink
matters more than an `error` for a missing description. This module turns every
finding into a 0-100 risk contribution and rolls findings up into a single
``risk_index``.

The model is intentionally small, documented, and deterministic - the same report
always produces the same numbers, so scores can be committed to a baseline, graphed
over time, and compared across teams without a model version conversation.

    risk    = min(100, base[severity] * exploitability[rule])
    index   = round(100 * (1 - exp(-sum(risk) / 100)))

``base`` encodes blast radius by severity. ``exploitability`` is above 1.0 only for
findings whose successful exploitation is direct and externally reachable: shell
execution, credential disclosure, SQL injection, unsafe deserialisation and SSRF.
The saturating exponential means the index climbs quickly with the first few serious
findings and then flattens, so it never pins at 100 because of noisy info-level drift.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

from mcp_doctor.models import Diagnostic, Severity

_BASE_BY_SEVERITY: dict[Severity, int] = {
    Severity.ERROR: 45,
    Severity.WARNING: 22,
    Severity.INFO: 8,
}

# Rules where a single instance implies direct, externally reachable impact.
EXPLOITABLE_RULE_IDS: frozenset[str] = frozenset(
    {
        "MCP005",  # shell execution / dynamic code eval
        "MCP006",  # SSRF
        "MCP009",  # hardcoded credential disclosure
        "MCP011",  # SQL injection
        "MCP013",  # unsafe deserialisation
    }
)
_EXPLOITABILITY_FACTOR = 1.5


def finding_risk(diagnostic: Diagnostic) -> int:
    """Return the 0-100 risk contribution of a single diagnostic."""
    base = _BASE_BY_SEVERITY[diagnostic.severity]
    if diagnostic.rule_id in EXPLOITABLE_RULE_IDS:
        base = int(round(base * _EXPLOITABILITY_FACTOR))
    return max(0, min(100, base))


def risk_index(risks: Sequence[int]) -> int:
    """Aggregate individual risk contributions into a saturating 0-100 index."""
    total = sum(risks)
    if total <= 0:
        return 0
    return max(0, min(100, round(100 * (1 - math.exp(-total / 100)))))


def risk_band(index: int) -> str:
    """Map a risk index onto a stable, human-facing band label."""
    if index >= 80:
        return "critical"
    if index >= 60:
        return "high"
    if index >= 30:
        return "medium"
    if index > 0:
        return "low"
    return "none"


def apply_risk(diagnostics: Sequence[Diagnostic]) -> tuple[tuple[Diagnostic, ...], int]:
    """Attach per-finding risk scores and return ``(diagnostics, risk_index)``."""
    scored = tuple(replace(d, risk=finding_risk(d)) for d in diagnostics)
    return scored, risk_index([d.risk for d in scored])
