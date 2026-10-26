"""Unit tests covering every built-in rule (MCP001 through MCP013) and suppressions."""

from __future__ import annotations

from pathlib import Path

import pytest

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
        "MCP010",
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


@pytest.mark.parametrize(
    "imports, expression, unsafe",
    [
        ("import pickle", "pickle.loads(data)", True),
        ("import pickle", "pickle.load(data)", True),
        ("import pickle as p", "p.loads(data)", True),
        ("from pickle import loads as decode", "decode(data)", True),
        ("import marshal", "marshal.loads(data)", True),
        ("import marshal", "marshal.load(data)", True),
        ("import shelve", "shelve.open(data)", True),
        ("import yaml", "yaml.load(data)", True),
        ("import yaml", "yaml.load_all(data)", True),
        ("import yaml", "yaml.unsafe_load(data)", True),
        ("import yaml", "yaml.unsafe_load_all(data)", True),
        ("import yaml as y", "y.load(data)", True),
        ("from yaml import load", "load(data)", True),
        ("import yaml", "yaml.load(data, Loader=yaml.Loader)", True),
        ("import yaml", "yaml.load(data, Loader=yaml.UnsafeLoader)", True),
        ("import yaml", "yaml.load(data, Loader=custom_loader)", True),
        ("import yaml", "yaml.safe_load(data)", False),
        ("import yaml", "yaml.safe_load_all(data)", False),
        ("import yaml", "yaml.load(data, Loader=yaml.SafeLoader)", False),
        ("import yaml", "yaml.load_all(data, Loader=yaml.CSafeLoader)", False),
        ("import yaml as y", "y.load(data, y.SafeLoader)", False),
        ("from yaml import load, SafeLoader as Safe", "load(data, Loader=Safe)", False),
        (
            "from yaml import load; from yaml.loader import SafeLoader",
            "load(data, SafeLoader)",
            False,
        ),
        ("import json as pickle", "pickle.loads(data)", False),
        ("from json import loads", "loads(data)", False),
        ("", "custom.loads(data)", False),
    ],
)
def test_deserialization_sinks_and_safe_loaders(
    tmp_path: Path, imports: str, expression: str, unsafe: bool
) -> None:
    source = tmp_path / "deserialize.py"
    source.write_text(f"{imports}\n@mcp.tool()\ndef decode(data: str):\n    return {expression}\n")
    report = scan_paths([source])
    findings = [d for d in report.diagnostics if d.rule_id == "MCP013"]
    assert len(findings) == int(unsafe)
    if findings:
        assert findings[0].severity.value == "error"
        assert findings[0].category.value == "security"
        assert findings[0].rule_slug == "unsafe-deserialization"
        assert findings[0].source_file == str(source)
        assert findings[0].tool_name == "decode"
        assert findings[0].line == 4
        assert report.tools[0].sinks[0].uses_param == "data"


def test_deserialization_function_local_import_and_suppression(tmp_path: Path) -> None:
    source = tmp_path / "local.py"
    source.write_text("""
@mcp.tool()
def unsafe(data):
    import pickle as p
    return p.loads(data)

@mcp.tool()
def suppressed(data):  # noqa: MCP013
    from pickle import loads as decode
    return decode(data)
""")
    report = scan_paths([source])
    findings = [d for d in report.diagnostics if d.rule_id == "MCP013"]
    assert [(d.tool_name, d.line) for d in findings] == [("unsafe", 5)]
