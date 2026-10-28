"""Unit tests for the Python AST, JSON, TypeScript and YAML tool manifest loaders."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_doctor.engine import discover_candidate_files, scan_paths
from mcp_doctor.loaders import (
    load_json_manifest,
    load_python_file,
    load_yaml_manifest,
    looks_like_tool_manifest,
)
from mcp_doctor.loaders.yaml_manifest import _invalid_yaml_issue
from mcp_doctor.models import IssueKind, ScanConfig, ScanStatus


def test_python_ast_loader_extracts_fastmcp_and_sdk_tools(tmp_path: Path) -> None:
    src = tmp_path / "server.py"
    src.write_text(
        '''
from mcp.types import Tool

class Server:
    def tool(self, name=None, description=None):
        def wrap(fn):
            return fn
        return wrap

mcp = Server()

@mcp.tool(name="search_tickets")
async def _search(ctx, query: str, limit: int = 25) -> list[str]:
    """Search issue tracker tickets by keyword.

    Args:
        query: Search string.
        limit: Max results.
    """
    limit = min(limit, 100)
    return [query] * limit

LEGACY_TOOL = Tool(
    name="ping_cluster",
    description="Check cluster control-plane reachability.",
    inputSchema={"type": "object", "properties": {}},
)
''',
        encoding="utf-8",
    )

    tools = load_python_file(src)
    assert [t.name for t in tools] == ["search_tickets", "ping_cluster"]
    first = tools[0]
    assert [p.name for p in first.parameters] == ["query", "limit"]
    assert first.parameters[0].required is True
    assert first.parameters[0].description == "Search string."
    assert first.parameters[1].required is False
    assert first.parameters[1].has_bounds is True


def test_python_ast_loader_handles_syntax_errors_gracefully(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def oops(:\n    pass\n", encoding="utf-8")
    assert load_python_file(broken) == []


def test_json_loader_supports_mcp_openai_and_anthropic_formats(tmp_path: Path) -> None:
    openai_file = tmp_path / "openai_tools.json"
    openai_file.write_text(
        json.dumps(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather_forecast",
                        "description": "Retrieve 3-day forecast for an IATA airport code.",
                        "parameters": {
                            "type": "object",
                            "required": ["airport"],
                            "properties": {
                                "airport": {"type": "string", "maxLength": 4},
                                "days": {"type": "integer", "maximum": 7},
                            },
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_json_manifest(openai_file)
    assert len(loaded) == 1
    assert loaded[0].name == "get_weather_forecast"
    assert len(loaded[0].parameters) == 2
    assert all(p.has_bounds for p in loaded[0].parameters)

    # Unrelated JSON (e.g. package.json) must produce 0 tools rather than false positives
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text('{"name": "my-app", "version": "1.0.0"}', encoding="utf-8")
    assert load_json_manifest(pkg_json) == []


def test_python_ast_expands_basemodel_and_typeddict(tmp_path: Path) -> None:
    src = tmp_path / "basemodel_server.py"
    src.write_text('''
from pydantic import BaseModel, Field
from typing import TypedDict

class SearchRequest(BaseModel):
    query: str = Field(..., description="Search term")
    limit: int = Field(default=10, le=100, description="Max results")

class SearchResp(TypedDict):
    items: list[str]
    total: int

@mcp.tool(name="search")
def search(req: SearchRequest, resp: SearchResp = None) -> list[str]:
    """Search."""
    return []
''')
    from mcp_doctor.loaders import load_python_file

    tools = load_python_file(src)
    assert len(tools) == 1
    names = [p.name for p in tools[0].parameters]
    assert "req.query" in names
    assert "req.limit" in names
    # TypedDict fields expanded
    assert "resp.items" in names
    assert "resp.total" in names


def test_typescript_loader_clean_fixture() -> None:
    from mcp_doctor.loaders import load_typescript_file

    fixture = Path(__file__).resolve().parent / "fixtures" / "clean_ts_server.ts"
    tools = load_typescript_file(fixture)
    assert len(tools) == 1
    assert tools[0].name == "search"
    names = [p.name for p in tools[0].parameters]
    assert "query" in names
    assert "limit" in names


def test_typescript_loader_vulnerable_fixture() -> None:
    from mcp_doctor.loaders import load_typescript_file

    fixture = Path(__file__).resolve().parent / "fixtures" / "vulnerable_ts_server.ts"
    tools = load_typescript_file(fixture)
    names = {t.name for t in tools}
    assert "run" in names
    assert "eval_script" in names
    # Check sinks detected
    run_tool = next(t for t in tools if t.name == "run")
    kinds = {s.kind for s in run_tool.sinks}
    assert "shell" in kinds


def test_typescript_loader_best_standard() -> None:
    from mcp_doctor.loaders import load_typescript_file

    clean = Path(__file__).resolve().parent / "fixtures" / "clean_ts_server.ts"
    vuln = Path(__file__).resolve().parent / "fixtures" / "vulnerable_ts_server.ts"
    clean_tools = load_typescript_file(clean)
    assert len(clean_tools) == 1
    t = clean_tools[0]
    assert t.name == "search"
    names = {p.name for p in t.parameters}
    assert names >= {"query", "limit", "filters"}
    # Best-standard: bounds detected on .max()
    limit_p = next(p for p in t.parameters if p.name == "limit")
    assert limit_p.has_bounds is True
    assert limit_p.required is False  # .optional()
    # Clean: no sinks
    assert t.sinks == ()

    vuln_tools = load_typescript_file(vuln)
    names = {t.name for t in vuln_tools}
    assert names == {"run", "eval_script", "fetch_no_timeout"}
    run_t = next(t for t in vuln_tools if t.name == "run")
    kinds = {s.kind for s in run_t.sinks}
    assert "shell" in kinds
    eval_t = next(t for t in vuln_tools if t.name == "eval_script")
    assert any(s.kind == "shell" and s.call_name == "eval" for s in eval_t.sinks)
    fetch_t = next(t for t in vuln_tools if t.name == "fetch_no_timeout")
    # MCP010 integration: timeout_missing reported
    assert any(s.timeout_missing for s in fetch_t.sinks if s.kind == "ssrf")


# ------------------------------------------------------------------------- YAML manifests
# YAML support is additive: same shapes as JSON, same rules, same scoring. The failure
# modes are the interesting part, because a manifest that is skipped silently is worse
# than one that fails loudly.


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


MCP_YAML = """\
tools:
  - name: run_shell
    description: Execute a shell command and return stdout.
    inputSchema:
      type: object
      required: [cmd]
      properties:
        cmd:
          type: string
        limit:
          type: integer
  - name: read_note
    description: Read a note from disk and return its contents.
    inputSchema:
      type: object
      properties:
        path:
          type: string
          description: Absolute path of the note.
"""


def test_yaml_loader_reads_mcp_shaped_manifests(tmp_path: Path) -> None:
    loaded = load_yaml_manifest(_write(tmp_path / "tools.yaml", MCP_YAML))
    assert [tool.name for tool in loaded] == ["run_shell", "read_note"]
    assert [param.name for param in loaded[0].parameters] == ["cmd", "limit"]
    assert loaded[1].parameters[0].description == "Absolute path of the note."
    # Line numbers come from list position, matching the JSON loader's convention.
    assert [tool.line for tool in loaded] == [2, 12]


def test_yaml_loader_reads_openai_and_bare_list_shapes(tmp_path: Path) -> None:
    openai_style = _write(
        tmp_path / "openai.yaml",
        """\
tools:
  - type: function
    function:
      name: get_weather
      description: Retrieve the 3-day forecast for an airport code.
      parameters:
        type: object
        properties:
          airport:
            type: string
            maxLength: 4
""",
    )
    loaded = load_yaml_manifest(openai_style)
    assert [tool.name for tool in loaded] == ["get_weather"]
    assert loaded[0].parameters[0].has_bounds is True

    bare_list = _write(
        tmp_path / "bare.yml",
        """\
- name: ping_host
  description: Send an ICMP echo to a host and report latency.
  input_schema:
    type: object
    properties:
      host: {type: string}
""",
    )
    assert [tool.name for tool in load_yaml_manifest(bare_list)] == ["ping_host"]


def test_yaml_loader_ignores_documents_that_are_not_tool_manifests(tmp_path: Path) -> None:
    """A YAML file with no tool entries is a clean skip, not an error."""
    for name, body in (
        ("compose.yml", "services:\n  web:\n    image: nginx\n"),
        ("mkdocs.yaml", "site_name: Docs\nnav:\n  - Home: index.md\n"),
        ("empty.yaml", ""),
        ("scalar.yaml", "just-a-string\n"),
    ):
        path = _write(tmp_path / name, body)
        issues: list = []
        assert load_yaml_manifest(path, issues=issues) == []
        assert issues == [], f"{name} should not produce a parse issue"


def test_yaml_loader_only_ever_uses_safe_loader(tmp_path: Path, monkeypatch) -> None:
    """Only ``SafeLoader`` may touch untrusted input.

    Asserting that ``yaml.load`` is never called would be wrong: PyYAML implements
    ``safe_load`` as ``load(stream, SafeLoader)``. What matters is *which* loader is
    passed, so that is what this pins — a regression to ``FullLoader`` or ``UnsafeLoader``
    (both of which construct arbitrary Python objects) fails here.
    """
    import yaml

    seen: list = []
    real_load = yaml.load

    def spy(stream, Loader=None, **kwargs):
        seen.append(Loader)
        return real_load(stream, Loader, **kwargs)

    def forbidden(name):
        def explode(*args, **kwargs):  # pragma: no cover - only fires on a regression
            raise AssertionError(f"{name}() was called on untrusted input")

        return explode

    monkeypatch.setattr(yaml, "load", spy)
    monkeypatch.setattr(yaml, "unsafe_load", forbidden("unsafe_load"))
    monkeypatch.setattr(yaml, "full_load", forbidden("full_load"))

    names = [tool.name for tool in load_yaml_manifest(_write(tmp_path / "t.yaml", MCP_YAML))]
    assert names == ["run_shell", "read_note"]
    assert len(seen) == 1
    assert issubclass(seen[0], yaml.SafeLoader)
    assert not issubclass(seen[0], yaml.UnsafeLoader)
    assert not issubclass(seen[0], yaml.FullLoader)


def test_yaml_loader_reports_invalid_yaml_with_a_line(tmp_path: Path) -> None:
    broken = _write(
        tmp_path / "broken.yaml",
        "tools:\n  - name: x\n    inputSchema:\n      type: object\n   properties:\n      a: [\n",
    )
    issues: list = []
    assert load_yaml_manifest(broken, issues=issues) == []
    assert len(issues) == 1
    assert issues[0].kind is IssueKind.INVALID_YAML
    assert issues[0].line == 5, "the issue should point at the offending line"
    assert "invalid YAML" in issues[0].message


def test_yaml_loader_reports_a_missing_dependency_instead_of_skipping(
    tmp_path: Path, monkeypatch
) -> None:
    """Without PyYAML the manifest must fail loudly, not look like a clean scan."""

    def missing(name, *args, **kwargs):
        if name == "yaml":
            raise ModuleNotFoundError("No module named 'yaml'")
        return real_import(name, *args, **kwargs)

    import builtins

    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", missing)

    issues: list = []
    assert load_yaml_manifest(_write(tmp_path / "tools.yaml", MCP_YAML), issues=issues) == []
    assert len(issues) == 1
    assert issues[0].kind is IssueKind.MISSING_DEPENDENCY
    assert "vexar-mcp[yaml]" in issues[0].message, "the message must say how to fix it"


def test_yaml_loader_reports_an_unreadable_file(tmp_path: Path) -> None:
    issues: list = []
    missing = tmp_path / "absent.yaml"
    assert load_yaml_manifest(missing, issues=issues) == []
    assert issues[0].kind is IssueKind.UNREADABLE


class TestYamlDiscovery:
    """Discovery decides what counts as a candidate, and that decision is the product."""

    def test_a_manifest_is_discovered_and_scanned(self, tmp_path: Path) -> None:
        _write(tmp_path / "tools.yaml", MCP_YAML)
        report = scan_paths([tmp_path], ScanConfig())
        assert [tool.name for tool in report.tools] == ["run_shell", "read_note"]
        assert report.coverage.candidate_files == 1
        assert report.status is ScanStatus.FINDINGS, "`run_shell` should trip a rule"

    def test_non_manifest_yaml_is_not_even_a_candidate(self, tmp_path: Path) -> None:
        """CI workflows and compose files must not inflate the candidate count."""
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        _write(
            tmp_path / ".github" / "workflows" / "ci.yml",
            "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: echo hi\n        with:\n          tools: cargo\n",
        )
        _write(tmp_path / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")
        assert discover_candidate_files([tmp_path], ScanConfig()) == []

        _write(tmp_path / "tools.yaml", MCP_YAML)
        candidates = discover_candidate_files([tmp_path], ScanConfig())
        assert [path.name for path in candidates] == ["tools.yaml"], (
            "only the manifest-shaped file may be a candidate"
        )

    def test_an_explicitly_named_yaml_file_bypasses_the_marker_check(self, tmp_path: Path) -> None:
        """`vexar-mcp scan tools.yaml` is an instruction, not a heuristic."""
        odd = _write(
            tmp_path / "odd.yaml",
            "- name: ping_host\n  description: Ping a host and report latency.\n"
            "  parameters:\n    type: object\n    properties:\n      host:\n        type: string\n",
        )
        assert discover_candidate_files([odd], ScanConfig()) == [odd]
        report = scan_paths([odd], ScanConfig())
        assert [tool.name for tool in report.tools] == ["ping_host"]

    def test_an_indented_tools_key_is_not_treated_as_a_manifest(self, tmp_path: Path) -> None:
        deep = _write(tmp_path / "workflow.yml", "jobs:\n  t:\n    with:\n        tools: cargo\n")
        assert not looks_like_tool_manifest(deep)


def test_yaml_findings_point_at_the_real_line(tmp_path: Path) -> None:
    """A finding on line 18 of a manifest must say line 18, not line 1.

    JSON manifests fall back to the entry's array position because a decoded document has
    no line information; YAML does have it, and reporting the tool's start line keeps every
    finding about one tool on the same location.
    """
    body = """tools:
  - name: lookup_note
    description: Look up a note by its identifier and return the text.
    inputSchema:
      type: object
      properties:
        note_id:
          type: string
          description: Identifier of the note to read.
        max_chars:
          type: integer
          description: Truncate the note to this many characters.
  - name: exec
    description: Runs a program. Vague on purpose so MCP015 fires.
    inputSchema:
      type: object
      properties:
        a:
          type: string
        b:
          type: string
"""
    path = _write(tmp_path / "tools.yaml", body)
    # Derived from the fixture, not hardcoded: the assertion is about *which* line, and a
    # literal here would quietly pin the wrong number the first time the fixture changes.
    expected_line = next(
        number
        for number, text in enumerate(body.splitlines(), start=1)
        if text.strip().startswith("- name: exec")
    )
    report = scan_paths([path], ScanConfig())
    findings = [d for d in report.diagnostics if d.rule_id == "MCP015"]
    assert len(findings) == 1
    assert findings[0].tool_name == "exec"
    assert findings[0].line == expected_line, (
        f"expected the tool's real line {expected_line}, got {findings[0].line}"
    )


def test_marker_check_treats_an_unreadable_file_as_not_a_manifest(tmp_path: Path) -> None:
    """Discovery must not raise on a path it cannot read; the loader reports that later."""
    assert looks_like_tool_manifest(tmp_path / "does-not-exist.yaml") is False


def test_invalid_yaml_without_a_position_still_reports_an_issue() -> None:
    """Some YAML errors carry no mark; the message must survive that."""

    class _Bare(Exception):
        problem = None

    issue = _invalid_yaml_issue(Path("m.yaml"), _Bare("stream ended early"), object())  # type: ignore[arg-type]
    assert issue.line is None
    assert "stream ended early" in issue.message
