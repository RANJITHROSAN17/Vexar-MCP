"""Partially understood tools must never turn into a clean bill of health."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vexar_mcp.cli import main
from vexar_mcp.engine import scan_paths
from vexar_mcp.loaders.ts_backends import tree_sitter_available
from vexar_mcp.models import IssueKind, ScanConfig, ScanStatus
from vexar_mcp.reporters import render_json, render_sarif


@pytest.mark.parametrize("parser", ["lexical", "tree-sitter"])
def test_names_and_imported_schema_are_reported_per_registration(
    tmp_path: Path, parser: str
) -> None:
    if parser == "tree-sitter" and not tree_sitter_available():
        pytest.skip("tree-sitter extra not installed")
    path = tmp_path / "server.ts"
    path.write_text(
        'import { schema } from "./schema";\n'
        'server.tool(dynamicName, "d", {}, () => {});\n'
        'server.registerTool("known", { inputSchema: schema }, () => {});\n'
        'server.tool("other", "d", schema, () => {});\n',
        encoding="utf-8",
    )
    report = scan_paths(
        [path],
        ScanConfig(ts_parser=parser, ignored_rules=frozenset(f"MCP{i:03d}" for i in range(1, 100))),
    )
    assert report.status is ScanStatus.PARTIAL
    assert report.summary.score is None
    assert report.coverage.files_analyzed == 1 and report.coverage.files_failed == 0
    assert report.ts_unresolved == 3
    assert [(i.kind, i.line) for i in report.parse_issues] == [
        (IssueKind.UNRESOLVED_TOOL, 2),
        (IssueKind.UNRESOLVED_TOOL, 3),
        (IssueKind.UNRESOLVED_TOOL, 4),
    ]
    assert not report.diagnostics
    data = json.loads(render_json(report))
    assert data["parse_issues"][0]["kind"] == "unresolved_tool"
    sarif = json.loads(render_sarif(report))["runs"][0]
    assert not sarif["results"]
    assert len(sarif["invocations"][0]["toolExecutionNotifications"]) == 3


def test_python_dynamic_names_and_imported_types(tmp_path: Path) -> None:
    path = tmp_path / "server.py"
    path.write_text(
        "from schemas import Query\nfrom typing import Any\n"
        "@mcp.tool(name=NAME)\ndef first(): pass\n"
        '@mcp.tool("safe")\ndef second(q: Query, other: Any): pass\n'
        "Tool(name=TOOL_NAME)\n",
        encoding="utf-8",
    )
    report = scan_paths([path])
    assert report.status is ScanStatus.PARTIAL
    assert [t.name for t in report.tools] == ["safe"]
    assert [(i.kind, i.line) for i in report.parse_issues] == [
        (IssueKind.UNRESOLVED_TOOL, 3),
        (IssueKind.UNRESOLVED_TOOL, 6),
        (IssueKind.UNRESOLVED_TOOL, 7),
    ]
    assert report.coverage.files_failed == 0


def test_parse_failure_and_multiple_unresolved_in_same_scan(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "server.ts").write_text(
        'server.tool(X, "d", {}, () => {});\nserver.tool(Y, "d", {}, () => {});\n', encoding="utf-8"
    )
    report = scan_paths([tmp_path])
    assert report.status is ScanStatus.PARTIAL
    assert report.coverage.candidate_files == 2
    assert report.coverage.files_analyzed == report.coverage.files_failed == 1
    assert len(report.parse_issues) == 3


def test_cli_fails_closed_and_can_be_overridden(tmp_path: Path) -> None:
    path = tmp_path / "server.ts"
    path.write_text('server.tool(NAME, "d", {}, () => {});', encoding="utf-8")
    assert main(["scan", str(path), "--fail-on", "never"]) == 3
    assert main(["scan", str(path), "--fail-on", "never", "--allow-incomplete"]) == 0


def test_inline_and_same_file_schemas_are_not_flagged_by_tree_sitter(tmp_path: Path) -> None:
    if not tree_sitter_available():
        pytest.skip("tree-sitter extra not installed")
    path = tmp_path / "server.ts"
    path.write_text(
        "const schema = { q: z.string() };\n"
        'server.tool("name", "description", schema, () => {});\n',
        encoding="utf-8",
    )
    report = scan_paths([path], ScanConfig(ts_parser="tree-sitter"))
    assert not report.parse_issues
    assert [p.name for p in report.tools[0].parameters] == ["q"]
