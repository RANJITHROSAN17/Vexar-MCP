"""Tests for analysis completeness: the fail-open regression suite.

The original sin of this project was that a file which could not be parsed produced no
tools, which produced no findings, which produced a score of 100 and exit code 0. These
tests exist so that can never come back. They are the most important tests in the
repository: a scanner whose "pass" cannot be trusted is worse than no scanner.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_doctor.cli import main
from mcp_doctor.engine import determine_status, scan_paths
from mcp_doctor.models import (
    IssueKind,
    ScanConfig,
    ScanStatus,
    Severity,
)
from mcp_doctor.reporters import (
    render_html,
    render_json,
    render_markdown,
    render_sarif,
    render_terminal,
)

CLEAN = Path(__file__).parent.parent / "examples" / "clean_server.py"
BROKEN_PY = "from mcp.server.fastmcp import FastMCP\n\nmcp = FastMCP('x')\n\n@mcp.tool()\ndef broken( -> str:\n    return ''\n"
BROKEN_JSON = '{"tools": [{"name": "run", "inputSchema": {"type": "object"'


def _broken_py(tmp_path: Path) -> Path:
    path = tmp_path / "broken_server.py"
    path.write_text(BROKEN_PY, encoding="utf-8")
    return path


def _broken_json(tmp_path: Path) -> Path:
    path = tmp_path / "tools.json"
    path.write_text(BROKEN_JSON, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


def test_syntax_error_is_never_a_perfect_score(tmp_path: Path) -> None:
    report = scan_paths([_broken_py(tmp_path)], ScanConfig())
    assert report.status is ScanStatus.ERROR
    assert report.summary.score is None, "an unparsable scan must not be graded"
    assert report.summary.grade == "N/A"
    assert report.coverage.files_failed == 1
    assert report.coverage.files_analyzed == 0
    assert [issue.kind for issue in report.parse_issues] == [IssueKind.SYNTAX_ERROR]


def test_malformed_json_is_never_a_perfect_score(tmp_path: Path) -> None:
    report = scan_paths([_broken_json(tmp_path)], ScanConfig())
    assert report.status is ScanStatus.ERROR
    assert report.summary.score is None
    assert [issue.kind for issue in report.parse_issues] == [IssueKind.INVALID_JSON]


def test_syntax_error_exits_three_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["scan", str(_broken_py(tmp_path))])
    captured = capsys.readouterr()
    assert code == 3, "incomplete analysis must be distinguishable from a clean pass"
    assert "NOT checked" in captured.out
    assert "syntax_error" in captured.out
    assert "analysis problem" in captured.err
    assert "100/100" not in captured.out


def test_partial_scan_is_not_graded(tmp_path: Path) -> None:
    good = tmp_path / "clean_server.py"
    good.write_text(CLEAN.read_text(encoding="utf-8"), encoding="utf-8")
    _broken_py(tmp_path)

    report = scan_paths([tmp_path], ScanConfig())
    assert report.status is ScanStatus.PARTIAL
    assert report.summary.score is None, "a partial scan must not present an A+"
    assert report.coverage.candidate_files == 2
    assert report.coverage.files_analyzed == 1
    assert report.coverage.files_failed == 1
    assert report.tools  # the readable file was still analyzed


def test_partial_scan_exits_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = tmp_path / "clean_server.py"
    good.write_text(CLEAN.read_text(encoding="utf-8"), encoding="utf-8")
    _broken_py(tmp_path)
    assert main(["scan", str(tmp_path)]) == 3
    assert "analysis incomplete" in capsys.readouterr().err


def test_allow_incomplete_downgrades_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _broken_py(tmp_path)
    assert main(["scan", str(tmp_path), "--allow-incomplete"]) == 0
    out = capsys.readouterr().out
    assert "PARTIAL" in out or "ERROR" in out
    # Even downgraded, the report must not claim a clean pass.
    assert "All checks passed" not in out


def test_no_matches_is_reported_and_exits_four_when_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "plain.py").write_text("x = 1\n", encoding="utf-8")
    report = scan_paths([tmp_path], ScanConfig())
    assert report.status is ScanStatus.NO_MATCHES
    assert report.summary.score is None
    capsys.readouterr()

    assert main(["scan", str(tmp_path)]) == 0
    assert main(["scan", str(tmp_path), "--fail-on-no-matches"]) == 4
    assert "no supported MCP tool" in capsys.readouterr().err


def test_clean_scan_is_still_clean() -> None:
    report = scan_paths([CLEAN], ScanConfig())
    assert report.status is ScanStatus.CLEAN
    assert report.summary.score == 100
    assert report.summary.grade == "A+"
    assert report.coverage.files_failed == 0


def test_findings_status_when_rules_fire() -> None:
    vulnerable = Path(__file__).parent.parent / "examples" / "vulnerable_server.py"
    report = scan_paths([vulnerable], ScanConfig())
    assert report.status is ScanStatus.FINDINGS
    assert report.summary.score is not None
    assert report.summary.errors > 0


def test_determine_status_table() -> None:
    from mcp_doctor.models import Category, Diagnostic, ToolSpec

    tool = ToolSpec(name="t", description="d" * 20, source_file="a.py", line=1)
    diag = Diagnostic(
        rule_id="MCP001",
        rule_slug="s",
        severity=Severity.ERROR,
        category=Category.CONTRACT,
        message="m",
        source_file="a.py",
        line=1,
        tool_name="t",
    )
    assert (
        determine_status(candidate_files=0, files_failed=0, tools=[], diagnostics=[])
        is ScanStatus.NO_MATCHES
    )
    assert (
        determine_status(candidate_files=3, files_failed=3, tools=[], diagnostics=[])
        is ScanStatus.ERROR
    )
    assert (
        determine_status(candidate_files=3, files_failed=1, tools=[tool], diagnostics=[])
        is ScanStatus.PARTIAL
    )
    assert (
        determine_status(candidate_files=1, files_failed=0, tools=[tool], diagnostics=[])
        is ScanStatus.CLEAN
    )
    assert (
        determine_status(candidate_files=1, files_failed=0, tools=[tool], diagnostics=[diag])
        is ScanStatus.FINDINGS
    )


# ---------------------------------------------------------------------------
# Every reporter must tell the truth
# ---------------------------------------------------------------------------


def test_json_exposes_status_coverage_and_null_score(tmp_path: Path) -> None:
    report = scan_paths([_broken_py(tmp_path)], ScanConfig())
    payload = json.loads(render_json(report))
    assert payload["status"] == "error"
    assert payload["analysis_complete"] is False
    assert payload["summary"]["score"] is None
    assert payload["summary"]["grade"] == "N/A"
    assert payload["coverage"]["files_failed"] == 1
    assert payload["parse_issues"][0]["kind"] == "syntax_error"


def test_terminal_never_says_all_checks_passed_on_incomplete(tmp_path: Path) -> None:
    good = tmp_path / "clean_server.py"
    good.write_text(CLEAN.read_text(encoding="utf-8"), encoding="utf-8")
    _broken_py(tmp_path)
    out = render_terminal(scan_paths([tmp_path], ScanConfig()))
    assert "All checks passed" not in out
    assert "NOT a clean result" in out


def test_markdown_never_claims_zero_issues_on_incomplete(tmp_path: Path) -> None:
    good = tmp_path / "clean_server.py"
    good.write_text(CLEAN.read_text(encoding="utf-8"), encoding="utf-8")
    _broken_py(tmp_path)
    out = render_markdown(scan_paths([tmp_path], ScanConfig()))
    assert "Zero security, schema, contract" not in out
    assert "PARTIAL" in out
    assert "not clean" in out


def test_html_shows_status_and_analysis_issues(tmp_path: Path) -> None:
    report = scan_paths([_broken_py(tmp_path)], ScanConfig())
    out = render_html(report)
    assert "Status: ERROR" in out
    assert "Analysis issues" in out
    assert "syntax_error" in out
    assert ">n/a<" in out or "n/a" in out


def test_sarif_reports_analysis_failures_as_notifications(tmp_path: Path) -> None:
    report = scan_paths([_broken_py(tmp_path)], ScanConfig())
    sarif = json.loads(render_sarif(report))
    run = sarif["runs"][0]
    invocation = run["invocations"][0]
    assert invocation["executionSuccessful"] is False
    notes = invocation["toolExecutionNotifications"]
    assert len(notes) == 1
    assert "syntax_error" in notes[0]["message"]["text"]
    assert run["properties"]["analysisComplete"] is False
    # A parse error is not a finding.
    assert run["results"] == []


def test_sarif_marks_successful_runs(tmp_path: Path) -> None:
    report = scan_paths([CLEAN], ScanConfig())
    run = json.loads(render_sarif(report))["runs"][0]
    assert run["invocations"][0]["executionSuccessful"] is True
    assert run["properties"]["analysisComplete"] is True


def test_score_floor_is_not_applied_to_an_ungraded_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--min-score` cannot compare against None, and must not crash doing it."""
    code = main(["scan", str(_broken_py(tmp_path)), "--min-score", "90"])
    assert code == 3
    assert "TypeError" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Discovery: exclude directories must be pruned, not walked
# ---------------------------------------------------------------------------


def test_excluded_directories_are_pruned_during_walk(tmp_path: Path) -> None:
    from mcp_doctor.engine import discover_candidate_files

    (tmp_path / "server.py").write_text("x = 1\n", encoding="utf-8")
    huge = tmp_path / "node_modules" / "pkg" / "deep"
    huge.mkdir(parents=True)
    for index in range(50):
        (huge / f"file{index}.js").write_text("// noise\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "junk.py").write_text("x = 1\n", encoding="utf-8")

    found = discover_candidate_files([tmp_path], ScanConfig())
    names = {p.name for p in found}
    assert names == {"server.py"}


def test_cache_directories_are_pruned_even_with_custom_excludes(tmp_path: Path) -> None:
    from mcp_doctor.engine import discover_candidate_files

    (tmp_path / "server.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text("x = 1\n", encoding="utf-8")
    config = ScanConfig(exclude_globs=("dist",))  # user replaced the default list
    names = {p.name for p in discover_candidate_files([tmp_path], config)}
    assert names == {"server.py"}


def test_custom_exclude_glob_prunes_directories(tmp_path: Path) -> None:
    from mcp_doctor.engine import discover_candidate_files

    (tmp_path / "server.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "gen.py").write_text("x = 1\n", encoding="utf-8")
    config = ScanConfig(exclude_globs=("generated",))
    names = {p.name for p in discover_candidate_files([tmp_path], config)}
    assert names == {"server.py"}


def test_coverage_counters_are_distinct(tmp_path: Path) -> None:
    """Candidate, analyzed, failed, with-tools and tools must not be the same number."""
    good = tmp_path / "clean_server.py"
    good.write_text(CLEAN.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "not_mcp.py").write_text("x = 1\n", encoding="utf-8")
    _broken_py(tmp_path)

    coverage = scan_paths([tmp_path], ScanConfig()).coverage
    assert coverage.candidate_files == 3
    assert coverage.files_analyzed == 2
    assert coverage.files_failed == 1
    assert coverage.files_with_tools == 1
    assert coverage.tools_analyzed == 2


def test_summary_files_scanned_means_files_analyzed(tmp_path: Path) -> None:
    good = tmp_path / "clean_server.py"
    good.write_text(CLEAN.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "plain.py").write_text("x = 1\n", encoding="utf-8")
    report = scan_paths([tmp_path], ScanConfig())
    # Two files were parsed; only one contained tools. The old field reported 1.
    assert report.summary.files_scanned == 2
    assert report.coverage.files_with_tools == 1


# ---------------------------------------------------------------------------
# Multi-output: one analysis, many renderings
# ---------------------------------------------------------------------------


def test_emit_renders_several_formats_from_one_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sarif_path = tmp_path / "out" / "report.sarif"
    html_path = tmp_path / "out" / "report.html"
    json_path = tmp_path / "out" / "report.json"
    code = main(
        [
            "scan",
            str(CLEAN),
            "--emit",
            f"sarif={sarif_path}",
            "--emit",
            f"html={html_path}",
            "--emit",
            f"json={json_path}",
        ]
    )
    capsys.readouterr()
    assert code == 0
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert html_path.read_text(encoding="utf-8").startswith("<!doctype html>")
    # Same snapshot across outputs: identical score and status.
    assert sarif["runs"][0]["properties"]["status"] == report["status"]


def test_emit_rejects_malformed_specs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", str(CLEAN), "--emit", "sarif"]) == 2
    assert "FORMAT=PATH" in capsys.readouterr().err

    assert main(["scan", str(CLEAN), "--emit", "yaml=x.yml"]) == 2
    assert "unknown --emit format" in capsys.readouterr().err


def test_emit_dash_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", str(CLEAN), "--emit", "json=-"]) == 0
    out = capsys.readouterr().out
    assert '"status": "clean"' in out


# ---------------------------------------------------------------------------
# Server-wide token budget (MCP014)
# ---------------------------------------------------------------------------


def test_server_budget_rule_thresholds() -> None:
    from mcp_doctor.models import ToolSpec
    from mcp_doctor.rules.token_rules import check_server_token_budget

    def tools(count: int, tokens: int) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=f"tool_{i}",
                description="d",
                source_file="f.py",
                line=i,
                estimated_tokens=tokens,
            )
            for i in range(count)
        ]

    assert check_server_token_budget(tools(10, 300), ScanConfig(max_server_tokens=4000)) == []
    assert check_server_token_budget(tools(8, 500), ScanConfig(max_server_tokens=4000)) == []

    findings = check_server_token_budget(tools(15, 300), ScanConfig(max_server_tokens=4000))
    assert len(findings) == 1
    assert findings[0].rule_id == "MCP014"
    assert "4500" in findings[0].message
    assert "4000-token server budget" in findings[0].message
    assert "tool_0" in findings[0].message


def test_server_budget_rule_requires_a_budget_and_tools() -> None:
    from mcp_doctor.models import ToolSpec
    from mcp_doctor.rules.token_rules import check_server_token_budget

    tool = ToolSpec(name="t", description="d", source_file="f.py", line=1, estimated_tokens=9999)
    assert check_server_token_budget([], ScanConfig(max_server_tokens=10)) == []
    assert check_server_token_budget([tool], ScanConfig(max_server_tokens=0)) == []


def test_server_budget_appears_in_reports() -> None:
    """The rule must reach every reporter, not just the terminal."""
    from mcp_doctor.engine import assemble_report
    from mcp_doctor.models import ToolSpec
    from mcp_doctor.rules.token_rules import check_server_token_budget

    tools = [
        ToolSpec(
            name=f"t{i}", description="d" * 40, source_file="s.py", line=i, estimated_tokens=400
        )
        for i in range(20)
    ]
    config = ScanConfig(max_server_tokens=1000)
    findings = check_server_token_budget(tools, config)
    report = assemble_report(tools, findings, config)
    assert any(d.rule_id == "MCP014" for d in report.diagnostics)
    assert "MCP014" in render_json(report)
    assert "MCP014" in render_markdown(report)
    assert "MCP014" in render_sarif(report)
    assert "MCP014" in render_terminal(report)


# ---------------------------------------------------------------------------
# Rule registry integrity
# ---------------------------------------------------------------------------


def test_register_rule_decorator_rejects_duplicate_ids() -> None:
    from mcp_doctor.models import Category
    from mcp_doctor.rules import register_rule

    with pytest.raises(ValueError, match="already registered"):

        @register_rule(
            rule_id="MCP005",  # already owned by the shell-execution rule
            slug="a-different-slug",
            severity=Severity.INFO,
            category=Category.SECURITY,
            summary="shadow",
            remediation="none",
        )
        def _shadow(tools, config):  # pragma: no cover - never registered
            return []


def test_every_registered_rule_has_complete_metadata() -> None:
    from mcp_doctor.rules import get_all_rules

    rules = get_all_rules()
    assert len(rules) == len({r.rule_id for r in rules}), "duplicate rule IDs"
    assert len(rules) == len({r.slug for r in rules}), "duplicate rule slugs"
    for rule in rules:
        assert rule.rule_id.startswith("MCP")
        assert len(rule.summary) >= 20, rule.rule_id
        assert len(rule.remediation) >= 20, rule.rule_id
        assert isinstance(rule.severity, Severity)


def test_discovery_does_not_walk_huge_excluded_trees(tmp_path: Path) -> None:
    """A monorepo's dependency tree must be pruned, not traversed and then filtered.

    The old implementation called ``rglob("*")`` first, so this fixture made the scanner
    visit every file under ``node_modules`` before discarding it.
    """
    import time

    from mcp_doctor.engine import discover_candidate_files

    relevant = tmp_path / "app"
    relevant.mkdir()
    for index in range(20):
        (relevant / f"server_{index}.py").write_text("x = 1\n", encoding="utf-8")

    noise = tmp_path / "node_modules" / "pkg" / "nested"
    noise.mkdir(parents=True)
    for index in range(600):
        (noise / f"file{index}.js").write_text("// noise\n", encoding="utf-8")

    started = time.perf_counter()
    found = discover_candidate_files([tmp_path], ScanConfig())
    elapsed = time.perf_counter() - started

    assert len(found) == 20
    # Generous bound: the point is the walk is pruned (milliseconds), not tuned to a machine.
    assert elapsed < 2.0, f"discovery took {elapsed:.2f}s; excluded directories are being walked"
