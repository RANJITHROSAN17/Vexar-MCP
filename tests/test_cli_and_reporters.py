"""Integration tests for CLI commands, TOML config loading, and all 4 output reporters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_doctor.cli import main

ROOT = Path(__file__).resolve().parent.parent


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
    assert "## 🩺 MCP Doctor Audit Summary" in md
    assert "| `MCP005` |" in md


def test_cli_rules_and_token_report_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["rules"]) == 0
    assert "MCP001" in capsys.readouterr().out

    assert main(["rules", "--format", "json"]) == 0
    rules_json = json.loads(capsys.readouterr().out)
    expected_ids = {f"MCP{i:03d}" for i in range(1, 9)} | {"MCP009", "MCP010", "MCP013"}
    assert {rule["rule_id"] for rule in rules_json} == expected_ids
    assert len(rules_json) == len(expected_ids)

    assert main(["token-report", str(ROOT / "examples" / "clean_server.py")]) == 0
    tok_out = capsys.readouterr().out
    assert "read_workspace_note" in tok_out


def test_toml_config_and_missing_target_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / ".mcp-doctor.toml"
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
