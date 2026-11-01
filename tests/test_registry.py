"""Tests for registry-file fleet auditing (`vexar-mcp audit-registry`).

The acceptance criteria for this feature are mostly about what happens when things go
wrong, so that is where the tests concentrate:

* a schema that fails closed — an ambiguous or malformed registry is an error, never a
  guess, and never an empty "clean" fleet;
* isolation — one server that cannot start must not cost you the results of the others;
* a fleet score that is the *worst* server rather than the average, because an average
  lets a vulnerable server hide behind healthy ones.

Two fixture servers are used. `tests/fixtures/mock_mcp_server.py` already speaks JSON-RPC
and has flags that make it misbehave; `CLEAN_SERVER` below is written to `tmp_path` by the
clean-server test so it can prove a well-formed server scores 100 without adding a
fixture file to the repository.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mcp_doctor.cli import main
from mcp_doctor.models import ScanConfig
from mcp_doctor.registry import (
    OUTCOME_AUDITED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    RegistryError,
    load_registry,
    parse_registry,
    run_fleet,
)

MOCK = Path(__file__).parent / "fixtures" / "mock_mcp_server.py"

#: A minimal MCP server with two well-described, bounded, typed tools. Auditing it
#: produces 100/100 — the "clean server" leg of the acceptance criteria.
CLEAN_SERVER = '''\
#!/usr/bin/env python3
"""Well-formed MCP server used as a fleet fixture."""
from __future__ import annotations

import json
import sys

TOOLS = [
    {
        "name": "lookup_invoice",
        "description": "Look up a single invoice by identifier and return its stored record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "string",
                    "description": "Stable invoice identifier such as INV-2025-0042.",
                    "maxLength": 64,
                }
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "describe_account",
        "description": "Describe an account, including its plan and billing contact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Opaque account identifier returned by lookup_invoice.",
                    "maxLength": 64,
                }
            },
            "required": ["account_id"],
        },
    },
]


def _send(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    method = msg.get("method")
    if method == "initialize":
        _send({
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "clean-reference-server", "version": "1.0.0"},
            },
        })
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": TOOLS}})
    elif msg.get("id") is not None:
        _send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {}})
'''

#: A command that cannot exist, so launching it fails before any protocol exchange.
MISSING = "vexar-mcp-test-no-such-executable"


def _clean_server(tmp_path: Path) -> Path:
    path = tmp_path / "clean_server.py"
    path.write_text(CLEAN_SERVER, encoding="utf-8")
    return path


def _write(tmp_path: Path, name: str, payload: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _native(*servers: object) -> dict[str, object]:
    return {"version": 1, "servers": list(servers)}


# --------------------------------------------------------------------- schema: happy path


def test_native_registry_parses_a_command_list() -> None:
    registry = parse_registry(_native({"name": "one", "command": ["python3", "s.py"]}))
    assert registry.source_format == "servers"
    assert [s.name for s in registry.servers] == ["one"]
    assert registry.servers[0].command == ("python3", "s.py")


def test_client_config_parses_command_and_args() -> None:
    """The Claude Desktop / Cursor shape: `command` is a string, `args` a list."""
    registry = parse_registry(
        {"mcpServers": {"files": {"command": "npx", "args": ["-y", "@acme/files"]}}}
    )
    assert registry.source_format == "mcpServers"
    assert registry.servers[0].command == ("npx", "-y", "@acme/files")


def test_client_config_keeps_env_and_cwd() -> None:
    registry = parse_registry(
        {"mcpServers": {"db": {"command": "db-server", "env": {"DSN": "sqlite://x"}, "cwd": "/srv"}}}
    )
    server = registry.servers[0]
    assert server.env == {"DSN": "sqlite://x"}
    assert server.cwd == "/srv"


def test_entry_timeout_overrides_the_default() -> None:
    registry = parse_registry(_native({"name": "slow", "command": ["x"], "timeout": 90}))
    assert registry.servers[0].timeout == 90.0


# ------------------------------------------------------------------ schema: fail-closed


def test_file_that_is_not_a_registry_names_what_it_found() -> None:
    with pytest.raises(RegistryError) as exc:
        parse_registry({"something": "else"}, path="reg.json")
    assert "reg.json" in str(exc.value)
    assert "something" in str(exc.value)


def test_both_shapes_at_once_is_refused_rather_than_guessed() -> None:
    with pytest.raises(RegistryError, match="both"):
        parse_registry({"servers": [], "mcpServers": {}})


def test_empty_registry_is_not_a_clean_fleet() -> None:
    """The dangerous default: 'nothing to audit' must never read as 'nothing wrong'."""
    with pytest.raises(RegistryError, match="no servers"):
        parse_registry({"servers": []})


def test_empty_client_config_is_not_a_clean_fleet() -> None:
    with pytest.raises(RegistryError, match="no servers"):
        parse_registry({"mcpServers": {}})


def test_duplicate_names_are_refused() -> None:
    with pytest.raises(RegistryError, match="duplicate"):
        parse_registry(
            _native({"name": "a", "command": ["x"]}, {"name": "a", "command": ["y"]})
        )


def test_names_differing_only_by_case_are_refused() -> None:
    with pytest.raises(RegistryError, match="differ only by case"):
        parse_registry(
            _native({"name": "api", "command": ["x"]}, {"name": "API", "command": ["y"]})
        )


def test_command_list_plus_args_is_an_error() -> None:
    with pytest.raises(RegistryError, match="not both"):
        parse_registry(_native({"name": "a", "command": ["x"], "args": ["y"]}))


def test_empty_command_list_is_an_error() -> None:
    with pytest.raises(RegistryError, match="empty"):
        parse_registry(_native({"name": "a", "command": []}))


def test_non_string_argv_element_is_an_error() -> None:
    with pytest.raises(RegistryError, match="non-empty string"):
        parse_registry(_native({"name": "a", "command": ["x", 3]}))


def test_env_value_must_be_a_string_and_the_error_says_what_to_do() -> None:
    with pytest.raises(RegistryError) as exc:
        parse_registry(_native({"name": "a", "command": ["x"], "env": {"PORT": 8080}}))
    message = str(exc.value)
    assert "PORT" in message
    assert 'quote it as "8080"' in message


def test_non_positive_timeout_is_an_error() -> None:
    with pytest.raises(RegistryError, match="greater than zero"):
        parse_registry(_native({"name": "a", "command": ["x"], "timeout": 0}))


def test_boolean_timeout_is_not_accepted_as_a_number() -> None:
    with pytest.raises(RegistryError, match="number of seconds"):
        parse_registry(_native({"name": "a", "command": ["x"], "timeout": True}))


def test_unknown_registry_version_is_refused() -> None:
    with pytest.raises(RegistryError, match="unsupported registry version"):
        parse_registry({"version": 99, "servers": [{"name": "a", "command": ["x"]}]})


def test_missing_file_is_a_registry_error(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="not found"):
        load_registry(tmp_path / "absent.json")


def test_invalid_json_reports_the_position(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"servers": [', encoding="utf-8")
    with pytest.raises(RegistryError) as exc:
        load_registry(path)
    assert "line" in str(exc.value) and "column" in str(exc.value)


def test_remote_server_is_skipped_not_dropped() -> None:
    """An SSE entry is well-formed but unauditable: it must still appear in the report."""
    registry = parse_registry({"mcpServers": {"remote": {"url": "https://example.test/sse"}}})
    assert registry.servers[0].unsupported_reason is not None
    assert "stdio" in (registry.servers[0].unsupported_reason or "")
    assert registry.auditable == ()


def test_explicit_http_transport_is_skipped() -> None:
    registry = parse_registry(
        {"mcpServers": {"remote": {"type": "http", "url": "https://example.test/mcp"}}}
    )
    assert registry.servers[0].unsupported_reason is not None


# ------------------------------------------------------------------- fleet: aggregation


def test_fleet_score_is_the_worst_server_not_the_average(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The central property. Four clean servers must not lift one vulnerable one."""
    clean = _clean_server(tmp_path)
    registry = parse_registry(
        _native(
            *[{"name": f"clean{i}", "command": [sys.executable, str(clean)]} for i in range(4)],
            {"name": "bad", "command": [sys.executable, str(MOCK)]},
        )
    )
    fleet = run_fleet(registry, ScanConfig(), default_timeout=20.0)
    scores = {
        a.name: a.score for a in fleet.audited if a.report is not None
    }
    assert scores["clean0"] == 100
    assert scores["bad"] is not None and scores["bad"] < 100
    assert fleet.score == min(s for s in scores.values() if s is not None)
    assert fleet.score == scores["bad"]
    assert fleet.mean_score is not None and fleet.mean_score > (fleet.score or 0)
    assert fleet.worst_server is not None and fleet.worst_server.name == "bad"
    # The grade must belong to the worst server, not to the average.
    assert fleet.grade == fleet.worst_server.report.summary.grade


def test_risk_index_is_the_highest_not_a_sum(tmp_path: Path) -> None:
    registry = parse_registry(
        _native(
            {"name": "a", "command": [sys.executable, str(MOCK)]},
            {"name": "b", "command": [sys.executable, str(MOCK)]},
        )
    )
    fleet = run_fleet(registry, ScanConfig(), default_timeout=20.0)
    single = run_fleet(
        parse_registry(_native({"name": "a", "command": [sys.executable, str(MOCK)]})),
        ScanConfig(),
        default_timeout=20.0,
    )
    # Same server twice is not twice the risk.
    assert fleet.risk_index == single.risk_index


# ------------------------------------------------------- fleet: isolation (a real run)


def test_a_broken_server_does_not_abort_the_fleet(tmp_path: Path) -> None:
    """Acceptance criterion: one server failing to start must not lose the others."""
    clean = _clean_server(tmp_path)
    registry = parse_registry(
        _native(
            {"name": "broken", "command": [MISSING]},
            {"name": "healthy", "command": [sys.executable, str(clean)]},
        )
    )
    fleet = run_fleet(registry, ScanConfig(), default_timeout=20.0)

    assert [a.outcome for a in fleet.audits] == [OUTCOME_FAILED, OUTCOME_AUDITED]
    broken, healthy = fleet.audits
    assert broken.report is None
    assert broken.error is not None and "not found" in broken.error
    # The healthy server was still audited, and graded.
    assert healthy.report is not None and healthy.score == 100
    assert fleet.score == 100
    # ...but the fleet is not "clean", because part of it was never looked at.
    assert fleet.complete is False
    assert fleet.status.is_complete is False


def test_a_hanging_server_is_isolated_too(tmp_path: Path) -> None:
    """A missing binary fails fast; a server that never answers is the nastier case.

    It must not consume the fleet: the deadline is per server, and the servers audited
    after it still get their own result.
    """
    clean = _clean_server(tmp_path)
    registry = parse_registry(
        _native(
            {"name": "silent", "command": [sys.executable, str(MOCK), "--silent"], "timeout": 1.5},
            {"name": "healthy", "command": [sys.executable, str(clean)]},
        )
    )
    fleet = run_fleet(registry, ScanConfig(), default_timeout=20.0)
    assert [a.outcome for a in fleet.audits] == [OUTCOME_FAILED, OUTCOME_AUDITED]
    assert fleet.failed[0].error is not None
    assert fleet.audits[1].score == 100
    assert fleet.complete is False


def test_partially_failing_fleet_reports_each_outcome(tmp_path: Path) -> None:
    clean = _clean_server(tmp_path)
    registry = parse_registry(
        _native(
            {"name": "clean", "command": [sys.executable, str(clean)]},
            {"name": "broken", "command": [MISSING]},
            {"name": "findings", "command": [sys.executable, str(MOCK)]},
        )
    )
    fleet = run_fleet(registry, ScanConfig(), default_timeout=20.0)
    outcomes = {a.name: a.outcome for a in fleet.audits}
    assert outcomes == {"clean": OUTCOME_AUDITED, "broken": OUTCOME_FAILED, "findings": OUTCOME_AUDITED}
    assert len(fleet.failed) == 1
    assert len(fleet.audited) == 2
    assert fleet.complete is False
    # The fleet score still reflects the audited half, and says so.
    assert fleet.score is not None


def test_a_skipped_server_makes_the_fleet_incomplete(tmp_path: Path) -> None:
    clean = _clean_server(tmp_path)
    registry_with_remote = parse_registry(
        {
            "mcpServers": {
                "clean": {"command": sys.executable, "args": [str(clean)]},
                "remote": {"url": "https://example.test/sse"},
            }
        }
    )
    fleet = run_fleet(registry_with_remote, ScanConfig(), default_timeout=20.0)
    assert [a.outcome for a in fleet.audits] == [OUTCOME_AUDITED, OUTCOME_SKIPPED]
    assert fleet.complete is False
    assert fleet.status.is_complete is False
    # The single audited server is still graded.
    assert fleet.score == 100


def test_fleet_where_nothing_could_be_audited_has_no_score() -> None:
    registry = parse_registry(
        _native({"name": "a", "command": [MISSING]}, {"name": "b", "command": [MISSING]})
    )
    fleet = run_fleet(registry, ScanConfig(), default_timeout=20.0)
    assert fleet.score is None
    assert fleet.grade == "N/A"
    assert fleet.worst_server is None
    assert fleet.status.value == "error"


def test_protocol_violation_is_a_finding_not_a_failure(tmp_path: Path) -> None:
    """A server that speaks bad protocol is still audited — its findings name the problem."""
    registry = parse_registry(
        _native({"name": "proto", "command": [sys.executable, str(MOCK), "--bad-protocol"]})
    )
    fleet = run_fleet(registry, ScanConfig(), default_timeout=20.0)
    assert fleet.audits[0].outcome == OUTCOME_AUDITED
    assert fleet.audits[0].report is not None
    rules = {d.rule_id for d in fleet.audits[0].report.diagnostics}
    assert "MCP012" in rules


# ------------------------------------------------------------------------- CLI contract


def _cli(tmp_path: Path, payload: object, *args: str) -> int:
    path = _write(tmp_path, "reg.json", payload)
    return main(["audit-registry", str(path), *args])


def test_cli_exits_3_when_a_server_could_not_be_audited(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = _clean_server(tmp_path)
    code = _cli(
        tmp_path,
        _native(
            {"name": "clean", "command": [sys.executable, str(clean)]},
            {"name": "broken", "command": [MISSING]},
        ),
        "--fail-on",
        "never",
    )
    assert code == 3
    captured = capsys.readouterr()
    assert "analysis incomplete" in captured.err
    assert "broken" in captured.err


def test_cli_allow_incomplete_downgrades_to_the_gate_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = _clean_server(tmp_path)
    code = _cli(
        tmp_path,
        _native(
            {"name": "clean", "command": [sys.executable, str(clean)]},
            {"name": "broken", "command": [MISSING]},
        ),
        "--fail-on",
        "never",
        "--allow-incomplete",
    )
    assert code == 0


def test_allow_incomplete_does_not_hide_a_real_gate_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--allow-incomplete downgrades exit 3; it must never turn a finding into a pass."""
    code = _cli(
        tmp_path,
        _native(
            {"name": "findings", "command": [sys.executable, str(MOCK)]},
            {"name": "broken", "command": [MISSING]},
        ),
        "--allow-incomplete",
        "--fail-on",
        "error",
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "gate failed" in err
    assert "analysis incomplete" not in err


def test_cli_gate_fails_on_a_fleet_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _cli(
        tmp_path,
        _native({"name": "findings", "command": [sys.executable, str(MOCK)]}),
        "--fail-on",
        "error",
    )
    assert code == 1
    assert "gate failed" in capsys.readouterr().err


def test_cli_min_score_gate_uses_the_worst_server(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The floor is checked against the fleet score, so a clean server cannot cover for a bad one.

    Note this deliberately does *not* pass ``--fail-on never``: that flag disables every
    gate including the score floor, which is its documented meaning.
    """
    clean = _clean_server(tmp_path)
    code = _cli(
        tmp_path,
        _native(
            {"name": "clean", "command": [sys.executable, str(clean)]},
            {"name": "findings", "command": [sys.executable, str(MOCK)]},
        ),
        "--min-score",
        "90",
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "below the required floor" in err
    # The reason names the server that set the fleet score, so the reader knows where to look.
    assert "findings" in err
    assert "clean" in err  # present in the fleet table, but not blamed in the reason


def test_cli_clean_fleet_passes(tmp_path: Path) -> None:
    clean = _clean_server(tmp_path)
    code = _cli(
        tmp_path,
        _native({"name": "clean", "command": [sys.executable, str(clean)]}),
        "--fail-on",
        "error",
    )
    assert code == 0


def test_cli_invalid_registry_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "reg.json"
    path.write_text('{"nope": 1}', encoding="utf-8")
    assert main(["audit-registry", str(path)]) == 2
    assert "registry" in capsys.readouterr().err.lower()


def test_cli_missing_registry_file_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["audit-registry", str(tmp_path / "nope.json")]) == 2
    assert "not found" in capsys.readouterr().err


def test_dry_run_launches_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A registry names commands. Reviewing the plan without executing it must be possible."""
    marker = tmp_path / "was-launched"
    script = tmp_path / "touch.py"
    script.write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    code = _cli(
        tmp_path,
        _native({"name": "side-effect", "command": [sys.executable, str(script)]}),
        "--dry-run",
    )
    assert code == 0
    assert not marker.exists(), "dry run executed the server command"
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "touch.py" in out


def test_dry_run_still_validates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "reg.json"
    path.write_text(json.dumps(_native({"name": "a", "command": []})), encoding="utf-8")
    assert main(["audit-registry", str(path), "--dry-run"]) == 2
    assert "empty" in capsys.readouterr().err


def test_cli_json_report_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = _clean_server(tmp_path)
    _cli(
        tmp_path,
        _native(
            {"name": "clean", "command": [sys.executable, str(clean)]},
            {"name": "broken", "command": [MISSING]},
        ),
        "--format",
        "json",
        "--fail-on",
        "never",
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "vexar-fleet/v1"
    assert payload["complete"] is False
    assert payload["coverage"] == {"servers": 2, "audited": 1, "failed": 1, "skipped": 0}
    assert payload["fleet"]["score_semantics"] == "minimum across audited servers"
    by_name = {entry["name"]: entry for entry in payload["servers"]}
    assert by_name["broken"]["outcome"] == "failed"
    assert "error" in by_name["broken"]
    # The nested report is the same document a single server would produce.
    assert by_name["clean"]["report"]["schema"] == "vexar-mcp-report/v1"


def test_cli_html_report_is_self_contained(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = _clean_server(tmp_path)
    _cli(
        tmp_path,
        _native({"name": "clean", "command": [sys.executable, str(clean)]}),
        "--format",
        "html",
    )
    document = capsys.readouterr().out
    assert document.startswith("<!DOCTYPE html>")
    assert document.rstrip().endswith("</html>")
    # No network references: the report must open offline, like the single-server one.
    assert "http://" not in document and "https://" not in document
    assert "<script" not in document


def test_report_writes_to_output_file(tmp_path: Path) -> None:
    clean = _clean_server(tmp_path)
    out = tmp_path / "out" / "fleet.json"
    _cli(
        tmp_path,
        _native({"name": "clean", "command": [sys.executable, str(clean)]}),
        "--format",
        "json",
        "--output",
        str(out),
    )
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == "vexar-fleet/v1"


# ------------------------------------------------------------------ untrusted text flow


def test_server_name_cannot_inject_terminal_escapes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A registry is user-supplied data; its names must not rewrite the terminal."""
    _cli(tmp_path, _native({"name": "evil\x1b[2Kname", "command": [MISSING]}), "--fail-on", "never")
    out = capsys.readouterr().out
    assert "\x1b[2K" not in out
    assert "evil" in out


def test_server_name_is_escaped_in_html(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _cli(
        tmp_path,
        _native({"name": "<img src=x onerror=alert(1)>", "command": [MISSING]}),
        "--format",
        "html",
        "--fail-on",
        "never",
    )
    document = capsys.readouterr().out
    assert "<img src=x onerror=alert(1)>" not in document
    assert "&lt;img" in document
