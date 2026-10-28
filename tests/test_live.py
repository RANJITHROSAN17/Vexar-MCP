"""Tests for live MCP server introspection over stdio JSON-RPC."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mcp_doctor.engine import scan_server
from mcp_doctor.live import (
    SUPPORTED_PROTOCOL_VERSIONS,
    LiveAuditError,
    LiveServerSpec,
    introspect_server,
    protocol_diagnostics,
)
from mcp_doctor.models import ScanConfig

MOCK = Path(__file__).parent / "fixtures" / "mock_mcp_server.py"


def _spec(*flags: str, timeout: float = 20.0) -> LiveServerSpec:
    return LiveServerSpec(command=(sys.executable, str(MOCK), *flags), timeout=timeout)


def test_introspect_returns_normalized_tools() -> None:
    result = introspect_server(_spec())
    names = [tool.name for tool in result.tools]
    assert names == ["run", "search_invoices", "export_report"]
    assert result.protocol_version == "2025-06-18"
    assert result.server_name == "mock-mcp-server"
    assert result.clean is True
    assert result.pages == 1
    assert result.duration_ms >= 0


def test_pagination_is_followed() -> None:
    result = introspect_server(_spec("--paginate"))
    assert result.pages == 2
    assert len(result.tools) == 3


def test_live_tools_carry_source_uri_through_pipeline() -> None:
    result = introspect_server(_spec())
    assert all(tool.source_file.startswith("mcp://") for tool in result.tools)
    assert any(tool.parameters for tool in result.tools)


def test_bad_protocol_is_reported_not_silently_accepted() -> None:
    result = introspect_server(_spec("--bad-protocol"))
    joined = " ".join(result.issues)
    assert "unsupported protocolVersion" in joined
    assert "tools` capability" in joined
    assert result.clean is False


def test_missing_input_schema_is_reported() -> None:
    result = introspect_server(_spec("--no-schema"))
    assert any("inputSchema" in issue for issue in result.issues)


def test_protocol_diagnostics_map_to_mcp012() -> None:
    result = introspect_server(_spec("--bad-protocol"))
    diagnostics = protocol_diagnostics(result)
    assert diagnostics
    assert {d.rule_id for d in diagnostics} == {"MCP012"}
    assert all(d.source_file.startswith("mcp://") for d in diagnostics)
    assert all(d.remediation for d in diagnostics)


def test_clean_server_has_no_protocol_diagnostics() -> None:
    assert protocol_diagnostics(introspect_server(_spec())) == ()


def test_scan_server_produces_a_full_report() -> None:
    report = scan_server([sys.executable, str(MOCK)], ScanConfig(), timeout=20.0)
    assert report.policy_name == "default"
    assert report.summary.tools_scanned == 3
    rule_ids = {d.rule_id for d in report.diagnostics}
    assert "MCP001" in rule_ids  # empty description on `run`
    assert report.summary.risk_index > 0
    assert report.summary.grade in {"A", "B", "C", "D", "F", "A+"}


def test_scan_server_reports_protocol_violations_in_report() -> None:
    report = scan_server([sys.executable, str(MOCK), "--bad-protocol"], ScanConfig(), timeout=20.0)
    assert "MCP012" in {d.rule_id for d in report.diagnostics}


def test_timeout_is_enforced_and_process_is_killed() -> None:
    with pytest.raises(LiveAuditError, match="timed out"):
        introspect_server(_spec("--silent", timeout=1.0))


def test_missing_executable_is_a_clear_error() -> None:
    spec = LiveServerSpec(command=("definitely-not-a-real-binary-xyz",), timeout=5.0)
    with pytest.raises(LiveAuditError, match="executable not found"):
        introspect_server(spec)


def test_empty_command_is_rejected() -> None:
    with pytest.raises(LiveAuditError, match="no command"):
        introspect_server(LiveServerSpec(command=()))
    with pytest.raises(LiveAuditError, match="empty command"):
        LiveServerSpec.from_command_line("   ")


def test_from_command_line_splits_without_shell_expansion() -> None:
    spec = LiveServerSpec.from_command_line(f"{sys.executable} {MOCK} --paginate", timeout=5.0)
    assert spec.command[0] == sys.executable
    assert spec.command[-1] == "--paginate"
    assert spec.protocol_version in SUPPORTED_PROTOCOL_VERSIONS


def test_non_json_stdout_banners_are_tolerated(tmp_path: Path) -> None:
    """Servers that print a banner to stdout before JSON must still be auditable."""
    server = tmp_path / "noisy.py"
    server.write_text(
        """
import json
import sys

print("booting noisy server v1", flush=True)

for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        payload = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "noisy", "version": "1"},
            },
        }
        print(json.dumps(payload), flush=True)
    elif message.get("method") == "tools/list":
        payload = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "tools": [
                    {
                        "name": "noisy_tool",
                        "description": "A noisy tool with a real description.",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            },
        }
        print(json.dumps(payload), flush=True)
""",
        encoding="utf-8",
    )
    result = introspect_server(LiveServerSpec(command=(sys.executable, str(server)), timeout=20.0))
    assert [t.name for t in result.tools] == ["noisy_tool"]
    assert result.clean is True
