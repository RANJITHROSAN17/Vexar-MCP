"""Regression tests for static MCP SDK extraction; no JavaScript is executed."""

from pathlib import Path

import pytest

from mcp_doctor.engine import discover_candidate_files, scan_paths
from mcp_doctor.loaders import load_typescript_file
from mcp_doctor.models import ScanConfig, ToolSpec


def load(tmp_path: Path, source: str, suffix: str = ".ts") -> list[ToolSpec]:
    path = tmp_path / ("server" + suffix)
    path.write_text(source, encoding="utf-8")
    return load_typescript_file(path)


@pytest.mark.parametrize("suffix", [".ts", ".js", ".jsx"])
@pytest.mark.parametrize("registration", ["tool", "registerTool"])
def test_sdk_registrations(tmp_path: Path, suffix: str, registration: str) -> None:
    schema = (
        '{query: z.string().describe("Search term"), limit: z.number().max(100), data: z.any()}'
    )
    args = (
        f'"Search records", {schema}'
        if registration == "tool"
        else ('{description: "Search records", inputSchema: ' + schema + "}")
    )
    [tool] = load(tmp_path, f'server.{registration}("search", {args}, async () => {{}});', suffix)
    assert (tool.name, tool.description) == ("search", "Search records")
    assert [(p.name, p.type_name, p.has_bounds) for p in tool.parameters] == [
        ("query", "string", False),
        ("limit", "number", True),
        ("data", "any", False),
    ]
    assert tool.parameters[0].description == "Search term"
    report = scan_paths([tmp_path])
    assert report.summary.tools_scanned == 1
    assert report.tools[0].name == "search"


def test_nested_schema_metadata_does_not_leak(tmp_path: Path) -> None:
    [tool] = load(
        tmp_path,
        """server.tool("search", "Search", {
  filters: z.object({
    category: z.string().optional().describe("Category"),
    count: z.number().max(4)
  }).describe("Filters"),
  tags: z.array(z.string().max(5)).optional(),
  nullable: z.string().nullable(),
  defaulted: z.number().default(2)
}, async () => {});""",
    )
    params = {p.name: p for p in tool.parameters}
    assert list(params) == [
        "filters",
        "filters.category",
        "filters.count",
        "tags",
        "nullable",
        "defaulted",
    ]
    assert params["filters"].type_name == "object"
    assert params["filters"].required and not params["filters"].has_bounds
    assert params["filters"].description == "Filters"
    assert not params["filters.category"].required
    assert params["filters.category"].description == "Category"
    assert params["filters.count"].has_bounds
    assert params["tags"].type_name == "list"
    assert not params["tags"].required and not params["tags"].has_bounds
    assert params["nullable"].required
    assert not params["defaulted"].required
    assert params["filters.category"].line == 3


def test_optional_parent_and_zod_object_schema(tmp_path: Path) -> None:
    [tool] = load(
        tmp_path,
        """server.registerTool("find", {
  description: "Find items", inputSchema: z.object({
    filters: z.object({name: z.string()}).optional()
  })
}, () => {});""",
    )
    assert [p.name for p in tool.parameters] == ["filters", "filters.name"]
    assert all(not p.required for p in tool.parameters)


@pytest.mark.parametrize("module", ["child_process", "node:child_process"])
@pytest.mark.parametrize("imported", ["exec", "execSync", "spawn", "execFile"])
def test_imported_shell_aliases(tmp_path: Path, module: str, imported: str) -> None:
    [tool] = load(
        tmp_path,
        f'''import {{ {imported} as runCommand }} from "{module}";
server.tool("run", "Run command", {{cmd: z.string()}}, async ({{cmd}}) => {{
  runCommand(cmd);
}});''',
    )
    assert [(s.kind, s.call_name, s.line, s.uses_param) for s in tool.sinks] == [
        ("shell", "runCommand", 3, "cmd")
    ]


@pytest.mark.parametrize(
    "declaration, call, kind",
    [
        ('import * as cp from "node:child_process";', "cp.exec(cmd)", "shell"),
        ('import cp from "child_process";', "cp.execFile(cmd)", "shell"),
        ("", "child_process.exec(cmd)", "shell"),
        ("", "eval(cmd)", "shell"),
        ("", "new Function(cmd)", "shell"),
        ('import { unlink as remove } from "node:fs";', "remove(cmd)", "filesystem"),
        ('import * as sandbox from "node:vm";', "sandbox.runInNewContext(cmd)", "shell"),
    ],
)
def test_sensitive_calls(tmp_path: Path, declaration: str, call: str, kind: str) -> None:
    [tool] = load(
        tmp_path, f'{declaration}\nserver.tool("run", {{cmd: z.string()}}, () => {call});'
    )
    assert len(tool.sinks) == 1
    assert tool.sinks[0].kind == kind
    assert tool.sinks[0].line == 2


def test_comments_strings_imports_and_neighbor_handlers_are_not_sinks(tmp_path: Path) -> None:
    tools = load(
        tmp_path,
        """import { exec } from "child_process";
// server.tool("fake", {}, () => exec("no"));
const text = 'server.registerTool("fake", {}, () => eval("no"))';
server.tool("clean", "eval(fake)", {query: z.string().describe("exec(query)")}, () => {
  // exec("ignored");
  const message = `eval("ignored")`;
  /* ) } eval("ignored"); */
  return {result: message};
});
server.tool("unsafe", "Run", {cmd: z.string()}, () => exec(cmd));
server.tool("also_clean", {}, () => {});
""",
    )
    assert [t.name for t in tools] == ["clean", "unsafe", "also_clean"]
    assert tools[0].sinks == tools[2].sinks == ()
    assert [p.name for p in tools[0].parameters] == ["query"]
    assert [(s.call_name, s.line) for s in tools[1].sinks] == [("exec", 10)]


def test_unimported_exec_is_not_assumed_to_be_child_process(tmp_path: Path) -> None:
    [tool] = load(tmp_path, 'server.tool("safe", {}, () => exec("custom helper"));')
    assert tool.sinks == ()


@pytest.mark.parametrize(
    "call, missing",
    [
        ("fetch(url)", True),
        ("fetch(url, {timeout: 1000})", True),
        ("fetch(url, {signal: AbortSignal.timeout(1000)})", False),
        ("fetch(url, {signal: controller.signal})", True),
        ("axios.get(url)", True),
        ("axios.get(url, {timeout: 1000})", False),
        ("axios.get(url, {timeout: 0})", True),
        ("axios.get(url, {timeout: null})", True),
        ("axios.get(url, {timeout: -1})", True),
        ("axios.post(url, {timeout: 1000})", True),
        ("axios.post(url, {}, {timeout: 1000})", False),
        ("axios.request({url, timeout: 1000})", False),
    ],
)
def test_javascript_timeouts(tmp_path: Path, call: str, missing: bool) -> None:
    [tool] = load(
        tmp_path,
        f"""server.tool("fetch_remote", {{url: z.string()}}, async ({{url}}) => {{
  return {call};
}});""",
    )
    assert len(tool.sinks) == 1
    sink = tool.sinks[0]
    assert (sink.kind, sink.line, sink.timeout_missing) == ("ssrf", 2, missing)
    assert sink.uses_param == "url"
    report = scan_paths([tmp_path])
    assert ("MCP010" in {d.rule_id for d in report.diagnostics}) == missing


def test_only_schema_parameters_are_extracted(tmp_path: Path) -> None:
    [tool] = load(
        tmp_path,
        """server.registerTool('find', {
  title: 'Search', description: 'Find, (matching) records',
  inputSchema: {'query': z.string().describe('A, (query)')}
}, async () => ({result: z.string()}));""",
    )
    assert tool.description == "Find, (matching) records"
    assert [(p.name, p.description) for p in tool.parameters] == [("query", "A, (query)")]


@pytest.mark.parametrize(
    "source",
    [
        'server.tool("broken", {name: z.string()}, () => {',
        'server.tool(dynamicName, {}, () => eval("no"));',
        'server.tool(`dynamic${name}`, {}, () => eval("no"));',
        '"server.tool(\\"fake\\", {}, () => {})"',
        '// server.tool("fake", {}, () => {})',
        "server.tool()",
    ],
)
def test_unsupported_or_incomplete_registrations_do_not_crash(tmp_path: Path, source: str) -> None:
    assert load(tmp_path, source) == []


def test_source_is_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    tools = load(
        tmp_path,
        f'''require("fs").writeFileSync("{marker}", "executed");
server.tool("safe", "Read text only", {{}}, () => {{}});''',
    )
    assert len(tools) == 1
    assert not marker.exists()


def test_discovery_excludes_declarations_and_dependencies(tmp_path: Path) -> None:
    source = 'server.tool("tool", {}, () => {});'
    for name in [
        "server.ts",
        "server.js",
        "server.jsx",
        "types.d.ts",
        "ignored.txt",
        "node_modules/dep.ts",
    ]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    found = discover_candidate_files([tmp_path], ScanConfig())
    assert {p.name for p in found} == {"server.ts", "server.js", "server.jsx"}
    assert (
        discover_candidate_files(
            [tmp_path / "types.d.ts", tmp_path / "node_modules/dep.ts"], ScanConfig()
        )
        == []
    )
    assert load_typescript_file(tmp_path / "types.d.ts") == []
    assert load_typescript_file(tmp_path / "ignored.txt") == []
    assert load_typescript_file(tmp_path / "missing.ts") == []


@pytest.mark.parametrize(
    "declaration, call",
    [
        ('import axios from "axios";', "axios(url, {timeout: 1000})"),
        ('import http from "axios";', "http({url, timeout: 1000})"),
        ('import * as http from "axios";', "http.get(url, {timeout: 1000})"),
        ('import { post as send } from "axios";', "send(url, {}, {timeout: 1000})"),
        ('import { get } from "axios";', "get(url, {timeout: 1000})"),
        ("", "axios.request(url, {timeout: 1000})"),
        ("", "globalThis.fetch(url, {signal: AbortSignal.timeout(1000)})"),
    ],
)
def test_http_imports_and_overloads(tmp_path: Path, declaration: str, call: str) -> None:
    [tool] = load(
        tmp_path, f'{declaration}\nserver.tool("read", {{url: z.string()}}, () => {call});'
    )
    assert len(tool.sinks) == 1
    assert tool.sinks[0].kind == "ssrf"
    assert not tool.sinks[0].timeout_missing


@pytest.mark.parametrize(
    "handler",
    [
        "function ({cmd}) { return eval(cmd); }",
        "async function ({cmd}) { return eval(cmd); }",
        "(({cmd}) => eval(cmd))",
        "async ({cmd}: {cmd: string}): Promise<unknown> => { return eval(cmd); }",
    ],
)
def test_inline_handler_forms_and_rule_integration(tmp_path: Path, handler: str) -> None:
    [tool] = load(tmp_path, f'server.tool("execute", {{cmd: z.string()}}, {handler});')
    assert [(s.call_name, s.uses_param) for s in tool.sinks] == [("eval", "cmd")]
    report = scan_paths([tmp_path])
    assert "MCP005" in {d.rule_id for d in report.diagnostics}


@pytest.mark.parametrize(
    "source",
    [
        'import "side-effects"; server.tool("read", {}, namedHandler);',
        'import {exec} from "unrelated"; server.tool("read", {}, () => exec("safe"));',
        'import type {exec} from "child_process"; server.tool("read", {}, () => exec("safe"));',
        'server.tool("read", {value: externalSchema}, () => {});',
        'server.registerTool("read", {description: dynamicDescription}, () => {});',
    ],
)
def test_unresolved_values_are_not_invented(tmp_path: Path, source: str) -> None:
    [tool] = load(tmp_path, source)
    assert tool.name == "read"
    assert tool.parameters == tool.sinks == ()


def test_excluded_explicit_files_and_duplicate_targets(tmp_path: Path) -> None:
    path = tmp_path / "server.ts"
    path.write_text('server.tool("read", {}, () => {});')
    assert discover_candidate_files([path, tmp_path, path], ScanConfig()) == [path]
    assert discover_candidate_files([path], ScanConfig(exclude_globs=("*.ts",))) == []
