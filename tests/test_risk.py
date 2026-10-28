"""Tests for the deterministic risk model."""

from __future__ import annotations

from mcp_doctor.models import Category, Diagnostic, Severity
from mcp_doctor.risk import apply_risk, finding_risk, risk_band, risk_index


def _diag(rule_id: str = "MCP001", severity: Severity = Severity.ERROR) -> Diagnostic:
    return Diagnostic(
        rule_id=rule_id,
        rule_slug="slug",
        severity=severity,
        category=Category.SECURITY,
        message="msg",
        source_file="a.py",
        line=1,
        tool_name="tool",
    )


def test_severity_ordering_drives_base_risk() -> None:
    assert finding_risk(_diag(severity=Severity.ERROR)) > finding_risk(
        _diag(severity=Severity.WARNING)
    )
    assert finding_risk(_diag(severity=Severity.WARNING)) > finding_risk(
        _diag(severity=Severity.INFO)
    )


def test_exploitable_rules_outrank_plain_rules_at_same_severity() -> None:
    shell = finding_risk(_diag(rule_id="MCP005", severity=Severity.ERROR))
    contract = finding_risk(_diag(rule_id="MCP001", severity=Severity.ERROR))
    assert shell > contract
    assert shell == 68  # 45 * 1.5, rounded


def test_risk_index_is_bounded_and_monotonic() -> None:
    assert risk_index([]) == 0
    assert risk_index([0, 0]) == 0
    assert risk_index([10]) < risk_index([50]) < risk_index([200])
    assert risk_index([100] * 50) == 100


def test_risk_index_is_deterministic() -> None:
    values = [68, 45, 22, 8]
    assert risk_index(values) == risk_index(list(reversed(values)))


def test_risk_bands() -> None:
    assert risk_band(0) == "none"
    assert risk_band(1) == "low"
    assert risk_band(30) == "medium"
    assert risk_band(60) == "high"
    assert risk_band(95) == "critical"


def test_apply_risk_scores_every_diagnostic() -> None:
    scored, index = apply_risk([_diag(), _diag(rule_id="MCP004", severity=Severity.INFO)])
    assert [d.risk for d in scored] == [45, 8]
    assert index > 0
    assert index == risk_index([45, 8])
