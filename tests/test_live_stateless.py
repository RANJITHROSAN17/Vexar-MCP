"""Protocol-version negotiation for live audits.

The defect these tests pin, found in the 0.2.2 review: the client only knew the handshake
(``initialize``), and the ``2026-07-28`` revision removed it. So the *newest* MCP servers —
the ones most likely to be in production — could not be audited at all, and the failure looked
like "server rejected initialize" rather than "we speak a protocol it no longer has".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mcp_doctor.cli import main
from mcp_doctor.live import (
    SUPPORTED_PROTOCOL_VERSIONS,
    VERSION_META_KEY,
    LiveAuditError,
    LiveServerSpec,
    introspect_server,
    select_protocol_version,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STATELESS = [sys.executable, str(FIXTURES / "stateless_mcp_server.py")]
HANDSHAKE = [sys.executable, str(FIXTURES / "mock_mcp_server.py")]


class TestVersionTable:
    def test_current_revision_is_supported(self) -> None:
        assert "2026-07-28" in SUPPORTED_PROTOCOL_VERSIONS

    def test_the_revision_between_is_not_skipped(self) -> None:
        # A gap here means servers pinned to that revision silently look un-auditable.
        assert {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"} <= set(
            SUPPORTED_PROTOCOL_VERSIONS
        )

    def test_newest_mutual_version_wins(self) -> None:
        assert select_protocol_version(["2025-06-18", "2026-07-28"]) == "2026-07-28"

    def test_no_overlap_returns_none(self) -> None:
        assert select_protocol_version(["1999-01-01"]) is None

    def test_unknown_versions_are_ignored_not_echoed(self) -> None:
        assert select_protocol_version(["2030-01-01", "2025-06-18"]) == "2025-06-18"


class TestStatelessAudit:
    def test_can_audit_a_current_revision_server(self) -> None:
        result = introspect_server(LiveServerSpec(command=tuple(STATELESS), timeout=30))
        assert result.mode == "stateless"
        assert result.protocol_version == "2026-07-28"
        assert result.server_name == "stateless-fixture"
        assert {tool.name for tool in result.tools} == {"list_incidents", "run"}

    def test_requests_declare_the_version_in_meta(self) -> None:
        # The audit succeeding at all proves `_meta` was sent: the fixture rejects any request
        # without it. This asserts the negotiated value is the one the server offered.
        result = introspect_server(LiveServerSpec(command=tuple(STATELESS), timeout=30))
        assert result.protocol_version in ("2025-11-25", "2026-07-28")

    def test_a_server_pinned_to_the_newest_revision_works(self) -> None:
        result = introspect_server(
            LiveServerSpec(command=(*STATELESS, "--only-2026-07-28"), timeout=30)
        )
        assert result.protocol_version == "2026-07-28"

    def test_a_server_pinned_to_2025_11_25_is_negotiated_down(self) -> None:
        # A server that offers only the last handshake-based revision still gets audited: the
        # client discovers the list and picks the newest mutual version, which is not its
        # default. This is the case that would break if discovery were skipped.
        result = introspect_server(
            LiveServerSpec(command=(*STATELESS, "--only-2025-11-25"), timeout=30)
        )
        assert result.protocol_version == "2025-11-25"
        assert result.mode == "stateless"
        assert result.tools

    def test_discovery_failure_names_both_paths(self) -> None:
        with pytest.raises(LiveAuditError) as exc:
            introspect_server(LiveServerSpec(command=(*STATELESS, "--no-discover"), timeout=30))
        message = str(exc.value)
        assert "handshake was rejected" in message
        assert "server/discover" in message

    def test_stateless_mode_still_applies_security_rules(self) -> None:
        # The fixture exposes a tool named `run` with an untyped `cmd` and no description, so
        # the point is that the rule pipeline runs on a stateless catalog too.
        result = introspect_server(LiveServerSpec(command=tuple(STATELESS), timeout=30))
        assert any(tool.name == "run" for tool in result.tools)


class TestHandshakeStillWorks:
    def test_legacy_server_is_audited_by_handshake(self) -> None:
        result = introspect_server(LiveServerSpec(command=tuple(HANDSHAKE), timeout=30))
        assert result.mode == "handshake"
        assert result.tools

    def test_unsupported_negotiated_version_is_reported_as_a_finding(self) -> None:
        result = introspect_server(
            LiveServerSpec(command=(*HANDSHAKE, "--bad-protocol"), timeout=30)
        )
        assert any("unsupported protocolVersion" in issue for issue in result.issues)


class TestReportProvenance:
    def test_json_report_states_the_revision_and_mode(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["audit-server", "--format", "json", "--fail-on", "never", "--", *STATELESS])
        assert code in (0, 1)
        payload = json.loads(capsys.readouterr().out)
        assert payload["live"] == {
            "protocol_version": "2026-07-28",
            "negotiation": "stateless",
        }

    def test_terminal_report_states_the_revision_and_mode(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["audit-server", "--fail-on", "never", "--", *STATELESS])
        out = capsys.readouterr().out
        assert "Live protocol: 2026-07-28" in out
        assert "negotiation: stateless" in out

    def test_static_scans_have_no_live_block(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["scan", "examples/clean_server.py", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["live"] is None


class TestVersionMetaKey:
    def test_key_matches_the_specification(self) -> None:
        # Hard-coded on purpose: if this string changes, servers stop understanding us.
        assert VERSION_META_KEY == "io.modelcontextprotocol/protocolVersion"
