"""End-to-end CLI tests for the product surfaces added in 0.2.0.

These assert on the contract CI depends on: exit codes, policy presets, baseline
gating, live auditing, and machine-readable output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mcp_doctor.cli import main

EXAMPLES = Path(__file__).parent.parent / "examples"
MOCK = Path(__file__).parent / "fixtures" / "mock_mcp_server.py"
CLEAN = str(EXAMPLES / "clean_server.py")
VULNERABLE = str(EXAMPLES / "vulnerable_server.py")


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "vexar-mcp" in capsys.readouterr().out


def test_scan_clean_file_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", CLEAN]) == 0
    assert "All checks passed" in capsys.readouterr().out


def test_scan_vulnerable_file_fails_with_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", VULNERABLE]) == 1
    captured = capsys.readouterr()
    assert "MCP005" in captured.out
    assert "gate failed" in captured.err


def test_fail_on_never_always_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", VULNERABLE, "--fail-on", "never"]) == 0
    capsys.readouterr()


def test_strict_prod_policy_escalates_warnings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file with warnings but no errors passes `default` and fails `strict-prod`."""
    target = tmp_path / "warnings_only.py"
    target.write_text(
        '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("warnings-only")


@mcp.tool()
def list_widgets(limit: int) -> str:
    """List widgets with a bounded limit parameter for agent pagination."""
    return str(limit)
''',
        encoding="utf-8",
    )
    # MCP004 is info-level; strict-prod fails on warning and above, so force a warning
    # with a policy that also drops the token budget to trigger MCP002.
    assert main(["scan", str(target), "--policy", "strict-prod", "--max-tokens", "1"]) == 1
    capsys.readouterr()


def test_policy_listing_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["policy", "--format", "json"]) == 0
    presets = json.loads(capsys.readouterr().out)
    names = [p["name"] for p in presets]
    assert names == ["default", "ci", "strict-prod", "enterprise-governance"]
    assert all("description" in p and "min_score" in p for p in presets)


def test_policy_listing_terminal(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["policy"]) == 0
    out = capsys.readouterr().out
    assert "strict-prod" in out
    assert "PROTECTED" in out


def test_print_config_includes_policy_and_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "vexar-mcp.toml").write_text('policy = "ci"\n', encoding="utf-8")
    source = tmp_path / "server.py"
    source.write_text("x = 1\n", encoding="utf-8")
    assert main(["scan", str(source), "--print-config"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["policy"] == "ci"
    assert payload["min_score"] == 80
    assert payload["config_file"].endswith("vexar-mcp.toml")


def test_ignoring_protected_rule_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", VULNERABLE, "--policy", "strict-prod", "--ignore", "MCP005"]) == 2
    assert "cannot be ignored" in capsys.readouterr().err


def test_baseline_workflow_gates_only_new_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "baseline.json"
    assert main(["scan", VULNERABLE, "--write-baseline", str(baseline)]) == 0
    assert baseline.is_file()
    capsys.readouterr()

    assert main(["scan", VULNERABLE, "--baseline", str(baseline), "--policy", "strict-prod"]) == 0
    out = capsys.readouterr().out
    assert "0 new" in out

    changed = tmp_path / "changed.py"
    changed.write_text(
        Path(VULNERABLE).read_text(encoding="utf-8")
        + "\n\ndef injected_handler(cmd: str) -> str:\n    import os\n\n    return os.popen(cmd).read()\n",
        encoding="utf-8",
    )
    assert main(["scan", str(changed), "--baseline", str(baseline)]) == 1
    assert "NEW FINDINGS" in capsys.readouterr().out


def test_baseline_write_and_show_subcommands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "b.json"
    assert main(["baseline", "write", VULNERABLE, "--output", str(baseline)]) == 0
    capsys.readouterr()

    assert main(["baseline", "show", str(baseline)]) == 0
    out = capsys.readouterr().out
    assert "vexar-mcp-baseline/v1" in out

    assert main(["baseline", "show", str(baseline), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entries"] > 0


def test_missing_baseline_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan", CLEAN, "--baseline", str(tmp_path / "nope.json")]) == 2
    assert "baseline file not found" in capsys.readouterr().err


def test_html_output_written_to_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_file = tmp_path / "reports" / "audit.html"
    assert main(["scan", VULNERABLE, "--format", "html", "--output", str(out_file)]) == 1
    text = out_file.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
    assert "Vexar-MCP" in text
    capsys.readouterr()


def test_json_output_includes_new_fields(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", VULNERABLE, "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "vexar-mcp-report/v1"
    assert payload["policy"]["name"] == "default"
    assert "risk_index" in payload["summary"]
    assert all("risk" in d and "fingerprint" in d for d in payload["diagnostics"])


def test_sarif_includes_security_severity_and_policy(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", VULNERABLE, "--format", "sarif"]) == 1
    sarif = json.loads(capsys.readouterr().out)
    run = sarif["runs"][0]
    assert run["properties"]["policy"] == "default"
    assert any("security-severity" in r["properties"] for r in run["tool"]["driver"]["rules"])
    assert all("partialFingerprints" in r for r in run["results"])


def test_markdown_includes_risk_and_policy(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", VULNERABLE, "--format", "markdown"]) == 1
    out = capsys.readouterr().out
    assert "**Policy:** `default`" in out
    assert "Risk" in out


def test_history_file_drives_sparkline_and_can_be_appended(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = tmp_path / "history.json"
    history.write_text("[70, 80]\n", encoding="utf-8")
    out_file = tmp_path / "r.html"
    assert (
        main(
            [
                "scan",
                CLEAN,
                "--format",
                "html",
                "--output",
                str(out_file),
                "--history",
                str(history),
                "--append-history",
            ]
        )
        == 0
    )
    assert "Score trend across 3 scans" in out_file.read_text(encoding="utf-8")
    assert json.loads(history.read_text(encoding="utf-8"))[-1] == 100
    capsys.readouterr()


def test_corrupt_history_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = tmp_path / "history.json"
    history.write_text("not json", encoding="utf-8")
    assert main(["scan", CLEAN, "--history", str(history)]) == 2
    assert "cannot read history file" in capsys.readouterr().err


def test_rules_json_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["rules", "--format", "json"]) == 0
    rules = json.loads(capsys.readouterr().out)
    ids = {r["rule_id"] for r in rules}
    assert "MCP005" in ids
    assert len(ids) == len(rules)  # no duplicate registrations


def test_missing_target_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan", str(tmp_path / "nope.py")]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_unknown_config_key_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "vexar-mcp.toml").write_text("nonsense = true\n", encoding="utf-8")
    source = tmp_path / "server.py"
    source.write_text("x = 1\n", encoding="utf-8")
    assert main(["scan", str(source)]) == 2
    assert "unknown configuration key" in capsys.readouterr().err


def test_rule_pack_flag_extends_the_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack = tmp_path / "acme.toml"
    pack.write_text(
        """
[[rule]]
id = "ACME900"
slug = "acme-requires-namespace"
severity = "warning"
category = "contract"
summary = "Tools must use the acme_ namespace."
remediation = "Rename the tool."
message = "Tool '{tool}' is not namespaced."
  [rule.match]
  name_regex = "^acme_"
  invert = true
""",
        encoding="utf-8",
    )
    target = tmp_path / "server.py"
    target.write_text(
        '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("acme")


@mcp.tool()
def list_widgets(limit: int) -> str:
    """List widgets for the agent, bounded by an explicit limit."""
    return str(limit)
''',
        encoding="utf-8",
    )
    assert main(["scan", str(target), "--rule-pack", str(pack), "--fail-on", "never"]) == 0
    out = capsys.readouterr().out
    assert "ACME900" in out


def test_broken_rule_pack_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack = tmp_path / "bad.toml"
    pack.write_text("[[rule]]\n", encoding="utf-8")
    assert main(["scan", CLEAN, "--rule-pack", str(pack)]) == 2
    assert "rule pack" in capsys.readouterr().err.lower()


def test_audit_server_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "audit-server",
                "--format",
                "json",
                "--fail-on",
                "never",
                "--",
                sys.executable,
                str(MOCK),
                "--paginate",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["tools_scanned"] == 3
    assert {d["rule_id"] for d in payload["diagnostics"]} >= {"MCP001"}


def test_audit_server_reports_protocol_violations(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "audit-server",
                "--format",
                "json",
                "--fail-on",
                "warning",
                "--",
                sys.executable,
                str(MOCK),
                "--bad-protocol",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert "MCP012" in {d["rule_id"] for d in payload["diagnostics"]}


def test_audit_server_without_command_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["audit-server"]) == 2
    assert "no server command" in capsys.readouterr().err


def test_token_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["token-report", str(EXAMPLES)]) == 0
    assert "EST. TOKENS" in capsys.readouterr().out


def test_fail_on_never_disables_the_score_floor_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--fail-on never` is report-only: it must not fail on the score floor either."""
    out_file = tmp_path / "report.sarif"
    code = main(
        [
            "scan",
            VULNERABLE,
            "--policy",
            "strict-prod",
            "--fail-on",
            "never",
            "--output",
            str(out_file),
            "--format",
            "sarif",
        ]
    )
    assert code == 0
    assert out_file.is_file()
    capsys.readouterr()


def test_score_floor_fails_the_run_when_threshold_is_active(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "warn_only.py"
    target.write_text(
        '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("warn-only")


@mcp.tool()
def list_reports(limit: int) -> str:
    """List reports, bounded by an explicit limit so the agent cannot flood context."""
    return str(limit)
''',
        encoding="utf-8",
    )
    # Informational drift alone is not an error, so the default threshold passes...
    assert main(["scan", str(target), "--fail-on", "error", "--min-score", "99"]) == 1
    out = capsys.readouterr()
    assert "score" in out.err or "below" in out.err
