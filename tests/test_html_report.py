"""Tests for the standalone HTML dashboard reporter."""

from __future__ import annotations

import re
from pathlib import Path

from mcp_doctor.baseline import build_baseline, compare
from mcp_doctor.engine import scan_paths
from mcp_doctor.reporters import render_html

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_html_is_a_complete_document() -> None:
    report = scan_paths([EXAMPLES / "vulnerable_server.py"])
    out = render_html(report)
    assert out.startswith("<!doctype html>")
    assert out.rstrip().endswith("</html>")
    assert "<title>" in out
    assert 'charset="utf-8"' in out


def test_html_has_no_external_assets() -> None:
    """The report must render offline and inside sandboxed iframes."""
    report = scan_paths([EXAMPLES / "vulnerable_server.py"])
    out = render_html(report)
    assert "<script src" not in out
    assert "<link" not in out
    assert "@import" not in out
    assert not re.search(r"https?://(?!json\.schemastore)", out)


def test_html_surfaces_score_risk_and_policy() -> None:
    report = scan_paths([EXAMPLES / "vulnerable_server.py"])
    out = render_html(report)
    assert f"{report.summary.score}<span" in out or str(report.summary.score) in out
    assert "Risk index" in out
    assert f"policy <strong>{report.policy_name}</strong>" in out
    assert "Context tax" in out


def test_html_rows_are_filterable() -> None:
    report = scan_paths([EXAMPLES / "vulnerable_server.py"])
    out = render_html(report)
    assert "data-severity='error'" in out
    assert 'data-filter="error"' in out
    assert 'id="q"' in out
    assert 'id="findings-data"' in out


def test_html_escapes_hostile_content() -> None:
    from mcp_doctor.models import Category, Diagnostic, ScanReport, ScanSummary, Severity

    nasty = Diagnostic(
        rule_id="MCP001",
        rule_slug="slug",
        severity=Severity.ERROR,
        category=Category.CONTRACT,
        message="<script>alert('xss')</script>",
        source_file="<img src=x onerror=alert(1)>.py",
        line=1,
        tool_name="</td><script>bad()</script>",
    )
    report = ScanReport(
        tools=(),
        diagnostics=(nasty,),
        summary=ScanSummary(
            files_scanned=1,
            tools_scanned=1,
            total_estimated_tokens=10,
            errors=1,
            warnings=0,
            infos=0,
            score=85,
            grade="B",
            risk_index=45,
        ),
    )
    out = render_html(report)
    assert "<script>alert" not in out
    assert "<img src=x" not in out
    assert "&lt;script&gt;" in out
    # Tool names are server-controlled: they must never appear as live markup.
    assert "<script>bad()" not in out
    assert "onerror=alert(1)>" not in out


def test_html_renders_delta_block() -> None:
    report = scan_paths([EXAMPLES / "vulnerable_server.py"])
    delta = compare(build_baseline(report), report)
    out = render_html(report, delta)
    assert 'class="delta"' in out
    assert "unchanged" in out


def test_html_renders_trend_sparkline_when_history_supplied() -> None:
    report = scan_paths([EXAMPLES / "clean_server.py"])
    without = render_html(report, history=[])
    with_history = render_html(report, history=[40, 55, 72, 88, 96])
    assert '<svg class="spark"' not in without
    assert '<svg class="spark"' in with_history
    assert "Score trend across 5 scans" in with_history


def test_html_handles_empty_scan() -> None:
    report = scan_paths([EXAMPLES / "clean_server.py"])
    out = render_html(report)
    assert "No findings" in out
    assert "A+" in out


def test_html_includes_tool_catalogue() -> None:
    report = scan_paths([EXAMPLES / "clean_server.py"])
    out = render_html(report)
    assert "Tool catalogue" in out
    assert any(tool.name in out for tool in report.tools)
