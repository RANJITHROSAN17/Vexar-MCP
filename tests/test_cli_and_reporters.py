"""Integration tests for CLI commands, TOML config loading, and all 5 output reporters."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mcp_doctor.cli import main
from mcp_doctor.models import Category, Diagnostic, ScanReport, ScanSummary, Severity
from mcp_doctor.reporters import render_github_annotations

ROOT = Path(__file__).resolve().parent.parent

# ::level key=value,key=value,key=value::message
_ANNOTATION_RE = re.compile(r"^::(?:error|warning|notice) (?:[\w-]+=[^:]*,?)+::.+$")


def test_cli_scan_clean_examples_passes(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "scan",
            str(ROOT / "examples" / "clean_server.py"),
            str(ROOT / "examples" / "tools_manifest.json"),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "100/100 (A+)" in out


def test_cli_scan_json_sarif_and_markdown_formats(capsys: pytest.CaptureFixture[str]) -> None:
    vuln = str(ROOT / "examples" / "vulnerable_server.py")

    rc_json = main(["scan", vuln, "--format", "json", "--fail-on", "error"])
    payload = json.loads(capsys.readouterr().out)
    assert rc_json == 1
    assert payload["summary"]["errors"] >= 1
    assert len(payload["diagnostics"]) >= 5

    rc_sarif = main(["scan", vuln, "--format", "sarif", "--fail-on", "never"])
    sarif = json.loads(capsys.readouterr().out)
    assert rc_sarif == 0
    assert sarif["version"] == "2.1.0"
    assert len(sarif["runs"][0]["results"]) >= 5

    rc_md = main(["scan", vuln, "--format", "markdown", "--fail-on", "never"])
    md = capsys.readouterr().out
    assert rc_md == 0
    assert "## Vexar-MCP Audit Summary" in md
    assert "| `MCP005` |" in md


def test_cli_rules_and_token_report_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["rules"]) == 0
    assert "MCP001" in capsys.readouterr().out

    assert main(["rules", "--format", "json"]) == 0
    rules_json = json.loads(capsys.readouterr().out)
    # MCP012 is live-only (emitted by audit-server), so the static catalog is 1-11, 13-15.
    expected_ids = {f"MCP{i:03d}" for i in range(1, 12)} | {"MCP013", "MCP014", "MCP015"}
    assert {rule["rule_id"] for rule in rules_json} == expected_ids
    assert len(rules_json) == len(expected_ids)

    assert main(["token-report", str(ROOT / "examples" / "clean_server.py")]) == 0
    tok_out = capsys.readouterr().out
    assert "read_workspace_note" in tok_out


def test_toml_config_and_missing_target_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / "vexar-mcp.toml"
    cfg.write_text('max-tool-tokens = 500\nignore = ["MCP001", "MCP005"]\n', encoding="utf-8")

    vuln = str(ROOT / "examples" / "vulnerable_server.py")
    rc = main(["scan", vuln, "--config", str(cfg), "--format", "json", "--fail-on", "never"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    emitted = {d["rule_id"] for d in data["diagnostics"]}
    assert "MCP001" not in emitted
    assert "MCP005" not in emitted

    rc_missing = main(["scan", str(tmp_path / "does_not_exist.py")])
    assert rc_missing == 2


def _report_with(*diagnostics: Diagnostic) -> ScanReport:
    """Wrap synthetic diagnostics in a ScanReport with a consistent summary."""
    return ScanReport(
        tools=(),
        diagnostics=diagnostics,
        summary=ScanSummary(
            files_scanned=1,
            tools_scanned=1,
            total_estimated_tokens=0,
            errors=sum(1 for d in diagnostics if d.severity is Severity.ERROR),
            warnings=sum(1 for d in diagnostics if d.severity is Severity.WARNING),
            infos=sum(1 for d in diagnostics if d.severity is Severity.INFO),
            score=0,
            grade="F",
        ),
    )


def test_render_github_annotations_maps_severities_and_escapes_messages() -> None:
    report = _report_with(
        Diagnostic(
            rule_id="MCP005",
            rule_slug="dangerous-shell-execution",
            severity=Severity.ERROR,
            category=Category.SECURITY,
            message='Sink "subprocess.run" used\r\nwith 100% of arguments',
            source_file="./server.py",
            line=24,
            tool_name="shell_exec",
            remediation="Prefer shell=False.",
        ),
        Diagnostic(
            rule_id="MCP003",
            rule_slug="untyped-or-any-parameter",
            severity=Severity.WARNING,
            category=Category.SCHEMA,
            message="Parameter 'q' is untyped.",
            source_file="src\\tools\\search.py",
            line=7,
            tool_name="search",
        ),
        Diagnostic(
            rule_id="MCP004",
            rule_slug="unbounded-collection-or-limit",
            severity=Severity.INFO,
            category=Category.TOKENS,
            message="No upper bound.",
            source_file="server.py",
            line=0,
            tool_name="list_items",
        ),
    )

    # Changed in 0.2.2: line endings are normalised (CRLF -> LF) by the output sanitiser
    # before the reporter escapes them, so a CRLF message now escapes as %0A rather than
    # %0D%0A. Both render as a line break in the annotation; normalising first is deliberate,
    # because it makes annotations identical on Windows and Linux runners.
    assert render_github_annotations(report).splitlines() == [
        (
            "::error file=server.py,line=24,title=MCP005 (dangerous-shell-execution)::"
            '[shell_exec] Sink "subprocess.run" used%0Awith 100%25 of arguments '
            "Fix: Prefer shell=False."
        ),
        (
            "::warning file=src/tools/search.py,line=7,title=MCP003 (untyped-or-any-parameter)::"
            "[search] Parameter 'q' is untyped."
        ),
        (
            "::notice file=server.py,line=1,title=MCP004 (unbounded-collection-or-limit)::"
            "[list_items] No upper bound."
        ),
    ]


def test_render_github_annotations_escapes_properties_and_keeps_literal_percent() -> None:
    report = _report_with(
        Diagnostic(
            rule_id="MCP999",
            rule_slug="weird, slug: v2",
            severity=Severity.ERROR,
            category=Category.CONTRACT,
            message="literal %0A stays data: 100%0A",
            source_file="a.py",
            line=3,
            tool_name="t",
        )
    )

    # `:` and `,` delimit the command header, so they are escaped in properties only,
    # while `%` is escaped first so an existing `%0A` sequence is never double-decoded.
    assert render_github_annotations(report) == (
        "::error file=a.py,line=3,title=MCP999 (weird%2C slug%3A v2)::"
        "[t] literal %250A stays data: 100%250A"
    )


def test_render_github_annotations_is_empty_for_clean_reports() -> None:
    assert render_github_annotations(_report_with()) == ""


def test_cli_scan_github_format_emits_inline_annotations(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)
    vuln = "examples/vulnerable_server.py"

    assert main(["scan", vuln, "--format", "github"]) == 1
    lines = capsys.readouterr().out.splitlines()

    assert len(lines) == 12
    assert lines[0] == (
        "::error file=examples/vulnerable_server.py,line=23,"
        "title=MCP001 (missing-tool-description)::[run] Tool 'run' has an empty or overly "
        "brief description (0 chars; minimum 12 recommended). Fix: Document purpose, inputs, "
        "and side effects in the tool docstring or `description`."
    )
    assert (
        "::error file=examples/vulnerable_server.py,line=25,title=MCP005 "
        "(dangerous-shell-execution)::[run] Tool 'run' invokes dangerous execution sink "
        "`subprocess.run(shell=True)` with parameter 'cmd'. Fix: Use `subprocess.run([cmd, "
        "arg], shell=False, check=True)` with validated arguments."
    ) in lines
    assert sum(1 for line in lines if line.startswith("::error ")) == 4
    assert sum(1 for line in lines if line.startswith("::warning ")) == 5
    # Three notices: MCP004 (unbounded `limit`) on `run`, and MCP015 on both tools -- one
    # finding per tool, listing its undocumented parameters.
    assert sum(1 for line in lines if line.startswith("::notice ")) == 3
    assert all(_ANNOTATION_RE.match(line) for line in lines)

    # The reporter only changes formatting: exit-code policy stays with --fail-on.
    assert main(["scan", vuln, "--format", "github", "--fail-on", "never"]) == 0
    capsys.readouterr()


def test_cli_scan_github_format_accepts_absolute_scan_paths(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # CI jobs frequently pass an absolute workspace path; annotations must still be
    # repo-relative, otherwise GitHub silently drops them.
    monkeypatch.chdir(ROOT)
    assert (
        main(["scan", str(ROOT / "examples" / "vulnerable_server.py"), "--format", "github"]) == 1
    )
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("::error file=examples/vulnerable_server.py,line=23,title=MCP001 ")
    assert "/home/" not in first


def test_render_github_annotations_relativizes_absolute_paths(tmp_path: Path) -> None:
    report = _report_with(
        Diagnostic(
            rule_id="MCP005",
            rule_slug="dangerous-shell-execution",
            severity=Severity.ERROR,
            category=Category.SECURITY,
            message="Shell injection.",
            source_file=str(tmp_path / "src" / "server.py"),
            line=12,
            tool_name="run",
        )
    )

    assert render_github_annotations(report, root=tmp_path) == (
        "::error file=src/server.py,line=12,title=MCP005 (dangerous-shell-execution)::"
        "[run] Shell injection."
    )


def test_cli_scan_github_format_is_silent_on_clean_scan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(
        [
            "scan",
            str(ROOT / "examples" / "clean_server.py"),
            str(ROOT / "examples" / "tools_manifest.json"),
            "--format",
            "github",
        ]
    )
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_render_github_annotations_keeps_paths_outside_root_absolute(tmp_path: Path) -> None:
    outside = tmp_path / "vendor" / "server.py"
    report = _report_with(
        Diagnostic(
            rule_id="MCP007",
            rule_slug="unconfined-filesystem-access",
            severity=Severity.WARNING,
            category=Category.SECURITY,
            message="Path traversal risk.",
            source_file=str(outside),
            line=5,
            tool_name="write_file",
        )
    )

    # A path outside the checkout is left as-is: rebasing it would invent a file
    # GitHub cannot match anyway.
    assert render_github_annotations(report, root=tmp_path / "repo") == (
        f"::warning file={outside.as_posix()},line=5,"
        "title=MCP007 (unconfined-filesystem-access)::[write_file] Path traversal risk."
    )


def test_legacy_config_filename_and_pyproject_table_still_resolve(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Projects configured before the Vexar-MCP rename must keep working.
    legacy = tmp_path / ".mcp-doctor.toml"
    legacy.write_text('ignore = ["MCP001"]\n', encoding="utf-8")
    vuln = str(ROOT / "examples" / "vulnerable_server.py")

    assert (
        main(["scan", vuln, "--config", str(legacy), "--format", "json", "--fail-on", "never"]) == 0
    )
    emitted = {d["rule_id"] for d in json.loads(capsys.readouterr().out)["diagnostics"]}
    assert "MCP001" not in emitted

    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[tool.vexar-mcp]\nignore = ["MCP005"]\n', encoding="utf-8"
    )
    src = project / "server.py"
    src.write_text((ROOT / "examples" / "vulnerable_server.py").read_text(encoding="utf-8"))
    assert main(["scan", str(src), "--format", "json", "--fail-on", "never"]) == 0
    emitted = {d["rule_id"] for d in json.loads(capsys.readouterr().out)["diagnostics"]}
    assert "MCP005" not in emitted
