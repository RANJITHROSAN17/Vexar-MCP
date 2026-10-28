"""Unit tests covering every built-in rule (MCP001-MCP011, MCP013-MCP015) and suppressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_doctor.engine import scan_paths
from mcp_doctor.models import Category, ParameterSpec, ScanConfig, Severity, ToolSpec
from mcp_doctor.rules import get_all_rules, get_rule_catalog
from mcp_doctor.rules.schema_rules import check_undocumented_parameters


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
        "MCP015",
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


# ---------------------------------------------------------------------------
# MCP009 — hardcoded-secret-in-tool (issue #1)
# ---------------------------------------------------------------------------
def _write_secret_server(tmp_path: Path, body: str, filename: str = "secret_server.py") -> Path:
    """Write a one-tool server; `body` lines start on source line 4."""
    source = tmp_path / filename
    source.write_text(
        "@mcp.tool()\n"
        "def call_api(query: str) -> str:\n"
        '    """Call an upstream API for the supplied query."""\n' + body + "\n",
        encoding="utf-8",
    )
    return source


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_A1b2C3d4E5f6G7h8I9j0",
        "ghp_VaUlT9x8Y7z6Q5w4R3t2",  # "vault" in a real key must not be treated as a reference
        "github_pat_11ABCDEFG0abcdefghijk",
        "sk-live-9f8e7d6c5b4a3210",
        "sk-a1",  # short keys still match a distinctive provider prefix
        "AKIAIOSFODNN7Q7Z4KLQ",
        "xoxb-1234567890-abcdefghijk",
        "AIzaSyD-1234567890abcdefghijklmnopqrstuv",
        "glpat-AbCdEfGh12345678",
        "Bearer 9f8e7d6c5b4a3210",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_mcp009_detects_known_token_prefixes(tmp_path: Path, secret: str) -> None:
    from mcp_doctor.models import Category, Severity

    source = _write_secret_server(tmp_path, f'    api_key = "{secret}"\n    return query')
    report = scan_paths([source])

    findings = [d for d in report.diagnostics if d.rule_id == "MCP009"]
    assert len(findings) == 1
    diagnostic = findings[0]
    assert diagnostic.rule_slug == "hardcoded-secret-in-tool"
    assert diagnostic.severity is Severity.ERROR
    assert diagnostic.category is Category.SECURITY
    assert diagnostic.line == 4
    assert diagnostic.tool_name == "call_api"
    assert diagnostic.source_file == str(source)
    # Reports must never become a second copy of the leaked credential.
    assert secret not in diagnostic.message
    assert "`api_key`" in diagnostic.message


@pytest.mark.parametrize(
    "statement",
    [
        'api_key = "YOUR_API_KEY"',
        'api_key = "example"',
        'api_key = "sk-example123"',
        'headers = {"Authorization": "Bearer <token>"}',
        'api_key = ""',
        'api_key = os.environ["OPENAI_API_KEY"]',
        'api_key = os.getenv("MY_SECRET_NAME", "")',
        'api_key = "${OPENAI_API_KEY}"',
        'api_key = "OPENAI_API_KEY"',
        'token = "Bearer " + os.environ["TOKEN"]',
        "api_key = None",
    ],
)
def test_mcp009_ignores_placeholders_and_env_lookups(tmp_path: Path, statement: str) -> None:
    source = _write_secret_server(tmp_path, f"    {statement}\n    return query")
    report = scan_paths([source])
    assert not [d for d in report.diagnostics if d.rule_id == "MCP009"]


@pytest.mark.parametrize(
    "statement, target",
    [
        ('api_key = "aB3dE5gH7jK9mN1"', "api_key"),
        ('client_secret: str = "9z8y7x6w5v4u3t2"', "client_secret"),
        ('config = {"auth_token": "q1w2e3r4t5y6u7"}', "auth_token"),
        ('client = Client(password="hunter2longpass")', "password"),
        ('self.api_key = "abc123XYZ789def"', "api_key"),
    ],
)
def test_mcp009_detects_sensitive_string_assignments(
    tmp_path: Path, statement: str, target: str
) -> None:
    source = _write_secret_server(tmp_path, f"    {statement}\n    return query")
    report = scan_paths([source])
    findings = [d for d in report.diagnostics if d.rule_id == "MCP009"]
    assert [(d.line, d.tool_name) for d in findings] == [(4, "call_api")]
    assert f"`{target}`" in findings[0].message


def test_mcp009_scopes_detection_to_tool_bodies_and_skips_docstrings(tmp_path: Path) -> None:
    source = tmp_path / "scoped_server.py"
    source.write_text(
        '''
MODULE_KEY = "sk-live-ABCDEF1234567890"  # module scope -> outside a tool handler


def helper():
    """Docs may mention sk-example without being a credential."""
    return "ghp_ZZZZ9999AAAA"  # plain function -> outside a tool handler


@mcp.tool()
def call_api(query: str) -> str:
    """Docs may show sk-DOCS123456 here without triggering MCP009."""
    return query
''',
        encoding="utf-8",
    )
    report = scan_paths([source])
    assert not [d for d in report.diagnostics if d.rule_id == "MCP009"]


def test_mcp009_respects_inline_noqa_suppression(tmp_path: Path) -> None:
    source = tmp_path / "suppressed_server.py"
    source.write_text(
        '''@mcp.tool()
def call_api(query: str) -> str:  # noqa: MCP009
    """Call an upstream API."""
    api_key = "ghp_A1b2C3d4E5f6G7h8I9j0"
    return query
''',
        encoding="utf-8",
    )
    report = scan_paths([source])
    # The loader still records the literal; the engine suppresses its diagnostic.
    assert len(report.tools[0].secret_literals) == 1
    assert not [d for d in report.diagnostics if d.rule_id == "MCP009"]


def test_mcp009_toolspec_never_stores_the_raw_secret(tmp_path: Path) -> None:
    secret = "sk-live-NEVERSTORE1234567890"
    source = _write_secret_server(tmp_path, f'    api_key = "{secret}"\n    return query')
    literals = scan_paths([source]).tools[0].secret_literals
    assert len(literals) == 1
    assert secret not in repr(literals)
    assert literals[0].line == 4
    assert literals[0].target == "api_key"


def test_mcp009_is_registered_once_as_error_security_rule() -> None:
    from mcp_doctor.models import Category, Severity
    from mcp_doctor.rules.base import get_all_rules

    matches = [rule for rule in get_all_rules() if rule.rule_id == "MCP009"]
    assert len(matches) == 1
    assert matches[0].slug == "hardcoded-secret-in-tool"
    assert matches[0].severity is Severity.ERROR
    assert matches[0].category is Category.SECURITY


def test_mcp009_ignores_documentation_examples_in_decorators(tmp_path: Path) -> None:
    source = tmp_path / "decorator_server.py"
    source.write_text(
        '''@mcp.tool(description="Pass a token such as sk-DOCS123456 to authenticate")
def call_api(query: str) -> str:
    """Call an upstream API for the supplied query."""
    return query
''',
        encoding="utf-8",
    )
    report = scan_paths([source])
    assert not [d for d in report.diagnostics if d.rule_id == "MCP009"]


def test_mcp009_reports_each_distinct_literal_once(tmp_path: Path) -> None:
    source = _write_secret_server(
        tmp_path,
        '    api_key = "ghp_A1b2C3d4E5f6G7h8I9j0"\n'
        '    headers = {"Authorization": "Bearer 9f8e7d6c5b4a3210"}\n'
        "    return query",
    )
    report = scan_paths([source])
    findings = [d for d in report.diagnostics if d.rule_id == "MCP009"]
    assert [d.line for d in findings] == [4, 5]
    assert len(report.tools[0].secret_literals) == 2
    assert all(d.severity.value == "error" for d in findings)


def test_sql_string_interpolation_flags_dynamic_statements(tmp_path: Path) -> None:
    """MCP011 fires on f-string, ``%``, ``+`` and ``.format`` SQL, and only those."""
    srv = tmp_path / "sql_server.py"
    srv.write_text(
        '''
import sqlite3

class MCP:
    def tool(self, name=None):
        return lambda f: f

mcp = MCP()
conn = sqlite3.connect("app.db")

@mcp.tool()
def lookup_fstring(user_id: str) -> str:
    """Look up a user by id."""
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM users WHERE id = '{user_id}'")
    return "ok"

@mcp.tool()
def lookup_percent(user_id: str) -> str:
    """Look up a user by id with percent formatting."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = '%s'" % user_id)
    return "ok"

@mcp.tool()
def lookup_concat(order_id: str) -> str:
    """Append an audit row."""
    cur = conn.cursor()
    cur.executemany("INSERT INTO audit VALUES ('" + order_id + "')", [])
    return "ok"

@mcp.tool()
def lookup_format(table: str) -> str:
    """Read one row from a caller-chosen table."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM {t} LIMIT 1".format(t=table))
    return "ok"

@mcp.tool()
def lookup_hint(prefix: str, user_id: str) -> str:
    """Assemble a statement from a caller-supplied fragment."""
    db.execute(f"{prefix} {user_id}")
    return "ok"
''',
        encoding="utf-8",
    )

    report = scan_paths([srv])
    flagged = {d.tool_name for d in report.diagnostics if d.rule_id == "MCP011"}
    assert flagged == {
        "lookup_fstring",
        "lookup_percent",
        "lookup_concat",
        "lookup_format",
        "lookup_hint",
    }
    errors = [d for d in report.diagnostics if d.rule_id == "MCP011"]
    assert all(d.severity.value == "error" for d in errors)
    assert all(d.rule_slug == "sql-string-interpolation" for d in errors)
    assert any("user_id" in d.message for d in errors)


def test_sql_parameterized_queries_and_non_sql_execute_are_not_flagged(tmp_path: Path) -> None:
    """Parameterized SQL, literals, and non-SQL ``.execute()`` calls stay clean."""
    srv = tmp_path / "sql_safe_server.py"
    srv.write_text(
        '''
import sqlite3

class MCP:
    def tool(self, name=None):
        return lambda f: f

mcp = MCP()
conn = sqlite3.connect("app.db")

@mcp.tool()
def lookup_parameterized(user_id: str) -> str:
    """Look up a user with a bound parameter."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return "ok"

@mcp.tool()
def lookup_named_binding(user_id: str) -> str:
    """Look up a user with named bindings."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = :user_id", {"user_id": user_id})
    return "ok"

@mcp.tool()
def literal_only() -> str:
    """Return a constant count."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    return "ok"

@mcp.tool()
def graphql_proxy(query_text: str) -> str:
    """Proxy a GraphQL query through a non-SQL client."""
    client.execute(f"query {query_text} {{ viewer {{ id }} }}")
    return "ok"

@mcp.tool()
def opaque_statement(statement: str) -> str:
    """Execute a statement that arrives already assembled."""
    cur = conn.cursor()
    cur.execute(statement)
    return "ok"

@mcp.tool()
def no_arguments() -> str:
    """Drive an argument-less execute call."""
    cur = conn.cursor()
    cur.execute()
    return "ok"
''',
        encoding="utf-8",
    )

    report = scan_paths([srv])
    assert [d for d in report.diagnostics if d.rule_id == "MCP011"] == []


# --------------------------------------------------------------------------- MCP015
# Undocumented tool parameters. Three properties matter and each has its own test: one finding
# per tool (not per parameter), silence below two parameters, and silence when documented.


def _tool_with_params(*pairs: tuple[str, str]) -> ToolSpec:
    return ToolSpec(
        name="search",
        description="Search the corpus.",
        source_file="server.py",
        line=10,
        parameters=tuple(
            ParameterSpec(name=name, type_name="str", description=desc, line=11 + index)
            for index, (name, desc) in enumerate(pairs)
        ),
    )


def test_mcp015_one_finding_per_tool_listing_every_undocumented_parameter() -> None:
    tool = _tool_with_params(("q", ""), ("n", ""), ("mode", ""))
    findings = check_undocumented_parameters([tool], ScanConfig())
    assert len(findings) == 1, "a wide schema is one documentation problem, not three"
    finding = findings[0]
    assert finding.rule_id == "MCP015"
    assert finding.severity is Severity.INFO
    assert finding.category is Category.SCHEMA
    assert "3 of 3 parameters" in finding.message
    for name in ("q", "n", "mode"):
        assert f"'{name}'" in finding.message


def test_mcp015_silent_for_a_single_parameter() -> None:
    # A single argument has nothing to disambiguate, so firing would be noise.
    assert check_undocumented_parameters([_tool_with_params(("x", ""))], ScanConfig()) == []


def test_mcp015_fires_on_a_single_undocumented_parameter_among_documented_ones() -> None:
    # The threshold is on parameter count, not on how many are undocumented: naming the one
    # neglected argument is the most useful message the rule can produce.
    findings = check_undocumented_parameters(
        [_tool_with_params(("x", "Why x."), ("y", ""), ("z", "Why z."))], ScanConfig()
    )
    assert len(findings) == 1
    assert "1 of 3 parameters" in findings[0].message
    assert "'y'" in findings[0].message


def test_mcp015_silent_when_every_parameter_is_documented() -> None:
    tool = _tool_with_params(("q", "Query text."), ("n", "Max results."))
    assert check_undocumented_parameters([tool], ScanConfig()) == []


def test_mcp015_lists_only_the_undocumented_ones() -> None:
    tool = _tool_with_params(("q", "Query text."), ("n", ""), ("mode", ""))
    findings = check_undocumented_parameters([tool], ScanConfig())
    assert len(findings) == 1
    assert "2 of 3 parameters" in findings[0].message
    assert "'q'" not in findings[0].message


def test_mcp015_truncates_a_very_wide_schema() -> None:
    tool = _tool_with_params(*[(f"p{i}", "") for i in range(9)])
    finding = check_undocumented_parameters([tool], ScanConfig())[0]
    assert "9 of 9 parameters" in finding.message
    assert "and 4 more" in finding.message, "the message must stay bounded"


def test_mcp015_whitespace_is_not_a_description() -> None:
    tool = _tool_with_params(("q", "   "), ("n", "\n\t"))
    assert len(check_undocumented_parameters([tool], ScanConfig())) == 1


def test_mcp015_anchors_on_the_first_undocumented_parameter() -> None:
    tool = _tool_with_params(("q", "Documented."), ("n", ""))
    finding = check_undocumented_parameters([tool], ScanConfig())[0]
    assert finding.line == 12, "should point at the undocumented parameter, not the tool"


def test_mcp015_fires_through_the_python_loader(tmp_path: Path) -> None:
    """End-to-end through `ast.parse` + docstring `Args:` extraction."""
    server = tmp_path / "srv.py"
    server.write_text(
        '''
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("s")

@mcp.tool()
def documented(customer_id: str, region: str) -> str:
    """Fetch a customer record.

    Args:
        customer_id: The numeric customer identifier.
        region: Two-letter data-residency region.
    """
    return customer_id + region

@mcp.tool()
def bare(alpha: str, beta: int) -> str:
    """Seven kinds of ambiguity with no argument documentation."""
    return str(alpha) + str(beta)
''',
        encoding="utf-8",
    )
    report = scan_paths([server], ScanConfig())
    mcp015 = [d for d in report.diagnostics if d.rule_id == "MCP015"]
    assert len(mcp015) == 1, "only the undocumented tool should fire"
    assert mcp015[0].tool_name == "bare"
    assert b"customer" not in mcp015[0].message.encode()


def test_mcp015_fires_through_the_json_manifest_loader(tmp_path: Path) -> None:
    """JSON Schema `description` on a property is what silences the rule."""
    manifest = tmp_path / "tools.json"
    manifest.write_text(
        """
{
  "tools": [
    {"name": "documented_manifest", "description": "Reads a file from disk.",
     "inputSchema": {"type": "object", "properties": {
        "path": {"type": "string", "description": "Filesystem path to read."},
        "mode": {"type": "string", "description": "Read mode: text or binary."}}}},
    {"name": "bare_manifest", "description": "Reads a file from disk.",
     "inputSchema": {"type": "object", "properties": {
        "path": {"type": "string"}, "mode": {"type": "string"}}}}
  ]
}
""",
        encoding="utf-8",
    )
    report = scan_paths([manifest], ScanConfig())
    fired = {d.tool_name for d in report.diagnostics if d.rule_id == "MCP015"}
    assert fired == {"bare_manifest"}


def test_mcp015_fires_through_the_typescript_loader(tmp_path: Path) -> None:
    """Both SDK idioms: the Zod raw-shape form and `registerTool` with an `inputSchema`."""
    server = tmp_path / "server.ts"
    server.write_text(
        """
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const server = new McpServer({ name: "ts", version: "1.0.0" });

server.tool("described", "Reads a file.", {
  path: z.string().describe("Filesystem path to read."),
  mode: z.string().describe("Read mode."),
}, async () => ({ content: [] }));

server.tool("undescribed", "Reads a file.", {
  path: z.string(), mode: z.string(),
}, async () => ({ content: [] }));

server.registerTool("registered_described", {
  description: "Fetches a URL.",
  inputSchema: z.object({
    url: z.string().describe("Target URL."),
    timeout: z.number().describe("Seconds."),
  }),
}, async () => ({ content: [] }));
""",
        encoding="utf-8",
    )
    report = scan_paths([server], ScanConfig())
    fired = {d.tool_name for d in report.diagnostics if d.rule_id == "MCP015"}
    assert fired == {"undescribed"}, (
        "`.describe()` documents a Zod param; a bare `z.string()` does not"
    )


def test_mcp015_is_in_the_public_catalog() -> None:
    assert get_rule_catalog()["MCP015"] is Category.SCHEMA
    definition = next(rule for rule in get_all_rules() if rule.rule_id == "MCP015")
    assert definition.severity is Severity.INFO
    assert definition.slug == "undocumented-parameter"
    assert definition.check is check_undocumented_parameters
