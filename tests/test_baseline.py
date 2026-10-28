"""Tests for baseline snapshots and delta gating."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_doctor.baseline import (
    BASELINE_SCHEMA,
    BaselineError,
    baseline_from_json,
    baseline_to_json,
    build_baseline,
    compare,
    fingerprint,
    normalize_source,
    read_baseline,
    write_baseline,
)
from mcp_doctor.engine import scan_paths
from mcp_doctor.models import Category, Diagnostic, ScanReport, ScanSummary, Severity

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLES = Path(__file__).parent.parent / "examples"


def _diag(
    rule_id: str = "MCP005",
    tool: str = "run",
    file: str = "examples/vulnerable_server.py",
    line: int = 10,
    message: str = "something",
) -> Diagnostic:
    return Diagnostic(
        rule_id=rule_id,
        rule_slug="slug",
        severity=Severity.ERROR,
        category=Category.SECURITY,
        message=message,
        source_file=file,
        line=line,
        tool_name=tool,
    )


def _report(diagnostics: tuple[Diagnostic, ...]) -> ScanReport:
    return ScanReport(
        tools=(),
        diagnostics=diagnostics,
        summary=ScanSummary(
            files_scanned=1,
            tools_scanned=1,
            total_estimated_tokens=0,
            errors=len(diagnostics),
            warnings=0,
            infos=0,
            score=50,
            grade="D",
        ),
    )


def test_fingerprint_ignores_line_and_message() -> None:
    a = _diag(line=10, message="first wording")
    b = _diag(line=999, message="completely different wording")
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_distinguishes_rule_tool_and_file() -> None:
    base = fingerprint(_diag())
    assert fingerprint(_diag(rule_id="MCP006")) != base
    assert fingerprint(_diag(tool="other")) != base
    assert fingerprint(_diag(file="other.py")) != base


def test_fingerprint_normalizes_path_prefixes() -> None:
    assert fingerprint(_diag(file="./examples/a.py")) == fingerprint(_diag(file="examples/a.py"))
    assert normalize_source(".\\a\\b.py") == "a/b.py"


def test_build_baseline_deduplicates_findings() -> None:
    baseline = build_baseline(_report((_diag(line=1), _diag(line=2), _diag(rule_id="MCP006"))))
    assert len(baseline.entries) == 2
    assert baseline.entries[0].fingerprint < baseline.entries[-1].fingerprint


def test_compare_detects_new_and_resolved() -> None:
    baseline = build_baseline(_report((_diag(), _diag(rule_id="MCP006"))))
    current = _report((_diag(), _diag(rule_id="MCP013")))
    delta = compare(baseline, current)

    assert len(delta.new_findings) == 1
    assert delta.new_findings[0].rule_id == "MCP013"
    assert len(delta.resolved) == 1
    assert delta.resolved[0].rule_id == "MCP006"
    assert delta.unchanged == 1


def test_compare_with_identical_reports_is_clean() -> None:
    report = _report((_diag(),))
    delta = compare(build_baseline(report), report)
    assert delta.new_findings == ()
    assert delta.resolved == ()
    assert delta.unchanged == 1


def test_delta_severity_filter() -> None:
    baseline = build_baseline(_report(()))
    current = _report((_diag(),))
    delta = compare(baseline, current)
    assert delta.new_at_or_above(Severity.ERROR.rank) == delta.new_findings
    assert delta.new_at_or_above(Severity.ERROR.rank + 1) == ()


def test_json_roundtrip(tmp_path: Path) -> None:
    report = scan_paths([EXAMPLES / "vulnerable_server.py"])
    baseline = build_baseline(report, policy="strict-prod", tool_version="9.9.9")
    path = tmp_path / "nested" / "baseline.json"
    write_baseline(path, baseline)

    restored = read_baseline(path)
    assert restored.schema == BASELINE_SCHEMA
    assert restored.policy == "strict-prod"
    assert restored.tool_version == "9.9.9"
    assert restored.fingerprints == baseline.fingerprints


def test_serialized_baseline_is_deterministic_and_sorted(tmp_path: Path) -> None:
    baseline = build_baseline(_report((_diag(), _diag(rule_id="MCP006"))))
    first = baseline_to_json(baseline)
    second = baseline_to_json(baseline_from_json(first))
    without_timestamp = [json.loads(first), json.loads(second)]
    for doc in without_timestamp:
        doc.pop("created_at")
    assert without_timestamp[0] == without_timestamp[1]


def test_missing_baseline_file_has_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="baseline file not found"):
        read_baseline(tmp_path / "nope.json")


def test_malformed_baselines_fail_closed() -> None:
    with pytest.raises(BaselineError, match="not valid JSON"):
        baseline_from_json("{oops")
    with pytest.raises(BaselineError, match="must be a JSON object"):
        baseline_from_json("[]")
    with pytest.raises(BaselineError, match="unsupported baseline schema"):
        baseline_from_json('{"schema": "vexar-mcp-baseline/v99", "entries": []}')
    with pytest.raises(BaselineError, match="must be a list"):
        baseline_from_json('{"schema": "vexar-mcp-baseline/v1", "entries": {}}')
    with pytest.raises(BaselineError, match="must be an object"):
        baseline_from_json('{"schema": "vexar-mcp-baseline/v1", "entries": [1]}')
    with pytest.raises(BaselineError, match="missing required field"):
        baseline_from_json('{"schema": "vexar-mcp-baseline/v1", "entries": [{"rule_id": "X"}]}')


def test_baseline_accepts_empty_entries() -> None:
    baseline = baseline_from_json('{"schema": "vexar-mcp-baseline/v1", "entries": []}')
    assert baseline.entries == ()
    assert baseline.fingerprints == frozenset()
