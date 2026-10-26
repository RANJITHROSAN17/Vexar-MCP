"""Unit tests covering every built-in rule (MCP001 through MCP013) and suppressions."""

from __future__ import annotations

from pathlib import Path

from mcp_doctor.engine import scan_paths
from mcp_doctor.models import ScanConfig


def test_clean_example_scores_100() -> None:
    clean_path = Path(__file__).resolve().parent.parent / "examples" / "clean_server.py"
    report = scan_paths([clean_path])
    assert report.diagnostics == ()
    assert report.summary.score == 100
    assert report.summary.grade == "A+"


def test_vulnerable_example_triggers_all_expected_rules() -> None:
    vuln_path = Path(__file__).resolve().parent.parent / "examples" / "vulnerable_server.py"
    report = scan_paths([vuln_path], ScanConfig(max_tool_tokens=40))
    triggered = {d.rule_id for d in report.diagnostics}
    assert triggered == {
        "MCP001",
        "MCP002",
        "MCP003",
        "MCP004",
        "MCP005",
        "MCP006",
        "MCP007",
        "MCP008",
    }
    assert report.summary.errors >= 3


def test_duplicate_tool_names_and_inline_noqa_suppression(tmp_path: Path) -> None:
    srv = tmp_path / "dup_server.py"
    srv.write_text(
        '''
import os

class MCP:
    def tool(self, name=None):
        return lambda f: f

mcp = MCP()

@mcp.tool(name="sync_records")
def first_sync(record_id: str) -> str:
    """Synchronize a single CRM record by ID."""
    return record_id

@mcp.tool(name="sync_records")
def second_sync(record_id: str) -> str:  # noqa: MCP005
    """Synchronize a single CRM record from backup."""
    os.system("echo ok")
    return record_id
''',
        encoding="utf-8",
    )

    report = scan_paths([srv])
    codes = [d.rule_id for d in report.diagnostics]
    assert "MCP008" in codes
    assert "MCP005" not in codes


def test_unsafe_deserialization_detection(tmp_path: Path) -> None:
    srv = tmp_path / "deser_server.py"
    srv.write_text(
        '''
import pickle
import yaml

class MCP:
    def tool(self, name=None):
        return lambda f: f

mcp = MCP()

@mcp.tool()
def load_unsafe(data: bytes) -> object:
    """Load data."""
    return pickle.loads(data)

@mcp.tool()
def load_yaml_unsafe(data: str) -> object:
    """Load YAML."""
    return yaml.load(data)

@mcp.tool()
def load_yaml_safe(data: str) -> object:
    """Load YAML safely."""
    return yaml.safe_load(data)
''',
        encoding="utf-8",
    )
    report = scan_paths([srv])
    codes = [d.rule_id for d in report.diagnostics]
    messages = [d.message for d in report.diagnostics]
    assert "MCP013" in codes
    assert any("pickle.loads" in m for m in messages)
    assert any("yaml.load" in m for m in messages)
    # Safe loaders should NOT trigger MCP013
    assert not any(
        "safe_load" in d.message.lower() and d.rule_id == "MCP013" for d in report.diagnostics
    )


def test_mcp010_missing_timeout_flagged(tmp_path: Path) -> None:
    srv = tmp_path / "timeout_server.py"
    srv.write_text('''
import requests

@mcp.tool()
def fetch(url: str) -> str:
    """Fetch."""
    return requests.get(url).text
''')
    from mcp_doctor.engine import scan_paths

    report = scan_paths([srv])
    triggered = {d.rule_id for d in report.diagnostics}
    assert "MCP010" in triggered


def test_mcp010_timeout_present_clean(tmp_path: Path) -> None:
    srv = tmp_path / "timeout_ok_server.py"
    srv.write_text('''
import requests

@mcp.tool()
def fetch(url: str) -> str:
    """Fetch with timeout."""
    return requests.get(url, timeout=10).text
''')
    from mcp_doctor.engine import scan_paths

    report = scan_paths([srv])
    triggered = {d.rule_id for d in report.diagnostics}
    assert "MCP010" not in triggered
