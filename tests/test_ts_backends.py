"""Tests for the optional tree-sitter TypeScript backend (`--ts-parser`).

The feature is an *opt-in front end*, so the tests are organised around the claims that
make it safe to ship:

1. **The default does not move.** The lexical backend is the default and every existing
   TypeScript test still passes unchanged; this file pins that selecting tree-sitter on
   source the lexical scanner already understood produces byte-identical results — same
   tools, same parameters, same sinks, same diagnostics, same score. If the two backends
   ever disagree about a file they can both read, the report becomes a function of which
   extra is installed, and that is worse than not having the feature.
2. **It resolves only what it can prove.** Computed names, same-file schema factories and
   referenced handlers resolve. Template strings with substitutions, cross-file imports,
   destructured bindings and functions with several returns do not — and are *counted*
   rather than silently dropped.
3. **It fails loudly.** Selecting the backend without the extra raises instead of quietly
   running the lexical scanner and reporting a coverage number it did not earn.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from vexar_mcp.cli import main
from vexar_mcp.engine import scan_paths
from vexar_mcp.loaders.ts_backends import (
    TreeSitterUnavailable,
    extract_tools,
    tree_sitter_available,
)
from vexar_mcp.loaders.typescript_loader import load_typescript
from vexar_mcp.models import ScanConfig

pytestmark = pytest.mark.skipif(
    not tree_sitter_available(),
    reason="optional tree-sitter extra is not installed (pip install -e '.[dev]')",
)

FIXTURES = Path(__file__).parent / "fixtures"

#: A file that uses every construct the two backends treat differently. Each row of
#: :data:`CONSTRUCTS` names one line region and what each backend is expected to see.
SOURCE = """\
import { exec } from "child_process";
const TOOL_NAME = "search_docs";
const DESC = "Find documents matching a query.";
const PLAIN = "read_file";
const TEMPLATED = `run_${process.env.STAGE}`;
const SHARED_SCHEMA = {
  q: z.string().max(100).describe("Query text"),
  limit: z.number().optional(),
};
function makeSchema() {
  return { path: z.string().describe("File path") };
}
async function namedHandler({ q }) {
  return exec(q);
}
server.tool(TOOL_NAME, DESC, SHARED_SCHEMA, async () => {});
server.tool(PLAIN, "Read a file.", makeSchema(), namedHandler);
server.tool(TEMPLATED, "Templated.", {}, () => {});
server.tool(UNBOUND, "Never declared.", {}, () => {});
server.registerTool(RT_ID, { description: RT_DESC, inputSchema: SHARED_SCHEMA }, () => {});
"""


def _write(tmp_path: Path, source: str, name: str = "server.ts") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


# ------------------------------------------------------- the default does not move


@pytest.mark.parametrize(
    "fixture", ["clean_ts_server.ts", "vulnerable_ts_server.ts"], ids=["clean", "vulnerable"]
)
def test_backends_agree_on_source_the_lexical_scanner_already_reads(fixture: str) -> None:
    """The acceptance criterion that matters: opting in must not change an existing verdict."""
    path = FIXTURES / fixture
    lexical = scan_paths([path], ScanConfig())
    tree_sitter = scan_paths([path], ScanConfig(ts_parser="tree-sitter"))

    def shape(report: object) -> list[tuple[str, int, tuple[str, ...]]]:
        return [
            (t.name, len(t.parameters), tuple(s.kind for s in t.sinks))
            for t in report.tools  # type: ignore[attr-defined]
        ]

    assert shape(lexical) == shape(tree_sitter)
    assert sorted((d.rule_id, d.tool_name, d.line) for d in lexical.diagnostics) == sorted(
        (d.rule_id, d.tool_name, d.line) for d in tree_sitter.diagnostics
    )
    assert lexical.summary.score == tree_sitter.summary.score
    assert lexical.summary.grade == tree_sitter.summary.grade


def test_lexical_remains_the_default(tmp_path: Path) -> None:
    """No flag means lexical: the dependency-free scanner, unchanged."""
    path = _write(tmp_path, SOURCE)
    assert load_typescript(path).backend == "lexical"
    assert ScanConfig().ts_parser == "lexical"
    # Every registration in the fixture passes a name through a variable or an undeclared
    # identifier, so the token scanner sees no tools at all — which is precisely the blind
    # spot this backend exists for.
    assert [t.name for t in scan_paths([tmp_path]).tools] == []


def test_the_default_report_does_not_claim_a_backend_it_did_not_use(tmp_path: Path) -> None:
    """A scan with no TypeScript in it must not report a TypeScript backend."""
    py = tmp_path / "server.py"
    py.write_text("def f():\n    pass\n", encoding="utf-8")
    assert scan_paths([tmp_path]).ts_parser is None


def test_unknown_backend_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, SOURCE)
    with pytest.raises(ValueError, match="unknown TypeScript parser backend"):
        load_typescript(path, parser="babelfish")


def test_missing_extra_fails_loudly_instead_of_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A silent fallback would report lexical coverage under a tree-sitter flag."""
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    path = _write(tmp_path, SOURCE)
    with pytest.raises(TreeSitterUnavailable) as exc:
        load_typescript(path, parser="tree-sitter")
    assert "Vexar-MCP[tree-sitter]" in str(exc.value)
    # ...and the lexical backend still runs with the extra removed: it returns an empty
    # list rather than an error, because a dependency-free install must keep working.
    assert load_typescript(path).tools == ()


# ---------------------------------------------------- what each backend can see


def test_computed_name_is_invisible_to_lexical_and_resolved_by_tree_sitter(tmp_path: Path) -> None:
    path = _write(tmp_path, SOURCE)
    assert [t.name for t in load_typescript(path).tools] == []
    resolved = {t.name for t in load_typescript(path, parser="tree-sitter").tools}
    assert resolved == {"search_docs", "read_file"}


def test_referenced_handler_sinks_are_found_by_tree_sitter(tmp_path: Path) -> None:
    """`server.tool(..., namedHandler)` hides an exec() from a token scanner."""
    path = _write(tmp_path, SOURCE)
    assert load_typescript(path).tools == ()
    tree = {t.name: t for t in load_typescript(path, parser="tree-sitter").tools}
    kinds = [sink.kind for sink in tree["read_file"].sinks]
    assert "shell" in kinds


def test_schema_factory_return_value_becomes_parameters(tmp_path: Path) -> None:
    path = _write(tmp_path, SOURCE)
    assert load_typescript(path).tools == ()
    tree = {t.name: t for t in load_typescript(path, parser="tree-sitter").tools}
    assert [(p.name, p.type_name) for p in tree["read_file"].parameters] == [("path", "string")]


def test_shared_schema_constant_becomes_parameters(tmp_path: Path) -> None:
    path = _write(tmp_path, SOURCE)
    tree = {t.name: t for t in load_typescript(path, parser="tree-sitter").tools}
    params = [
        (p.name, p.type_name, p.has_bounds, p.required) for p in tree["search_docs"].parameters
    ]
    assert params == [("q", "string", True, True), ("limit", "number", False, False)]
    assert tree["search_docs"].parameters[0].description == "Query text"


# ------------------------------------------- what neither backend will invent


@pytest.mark.parametrize(
    ("label", "source", "reason"),
    [
        (
            "template with a substitution",
            'const ID = `tool_${stage}`;\nserver.tool(ID, "d", {}, () => {});',
            "the value depends on runtime state",
        ),
        (
            "undeclared identifier",
            'server.tool(NEVER_DECLARED, "d", {}, () => {});',
            "there is no binding to read",
        ),
        (
            "imported constant",
            'import { ID } from "./ids.js";\nserver.tool(ID, "d", {}, () => {});',
            "cross-file resolution is out of scope",
        ),
        (
            "destructured binding",
            'const { ID } = config;\nserver.tool(ID, "d", {}, () => {});',
            "a destructuring pattern binds more than one name",
        ),
    ],
)
def test_unprovable_names_are_counted_not_invented(
    tmp_path: Path, label: str, source: str, reason: str
) -> None:
    path = _write(tmp_path, source)
    extraction = load_typescript(path, parser="tree-sitter")
    assert extraction.tools == (), f"{label}: {reason}"
    assert extraction.unresolved == 1, f"{label}: should be counted as unresolved, not dropped"


def test_a_resolvable_and_an_unresolvable_registration_are_both_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, SOURCE)
    extraction = load_typescript(path, parser="tree-sitter")
    # Two registrations resolve (a constant and a literal); three do not — the templated
    # name, the undeclared identifier, and the registerTool call whose id and description
    # are both unresolved.
    assert len(extraction.tools) == 2
    assert extraction.unresolved == 3
    # The lexical scanner sees all five registrations but cannot resolve their names.
    lexical = load_typescript(path)
    assert lexical.tools == ()
    assert lexical.unresolved == 5


def test_resolution_chain_is_bounded(tmp_path: Path) -> None:
    """A long alias chain must stop, not recurse forever."""
    chain = "const A0 = A1;\n" + "".join(f"const A{i} = A{i + 1};\n" for i in range(1, 20))
    source = chain + 'const A20 = "deep";\nserver.tool(A0, "d", {}, () => {});'
    extraction = load_typescript(_write(tmp_path, source), parser="tree-sitter")
    assert extraction.tools == ()
    assert extraction.unresolved == 1


def test_self_referential_binding_terminates(tmp_path: Path) -> None:
    source = 'const LOOP = LOOP;\nserver.tool(LOOP, "d", {}, () => {});'
    extraction = load_typescript(_write(tmp_path, source), parser="tree-sitter")
    assert extraction.unresolved == 1


# ------------------------------------------------------------- correctness edges


def test_non_ascii_source_maps_offsets_correctly(tmp_path: Path) -> None:
    """tree-sitter reports byte offsets; the analyser indexes characters.

    A file with multibyte text before a registration is where a naive offset mapping
    silently misplaces every finding, so it gets its own test.
    """
    source = (
        "// Café ☕ — naïve résumé, 日本語のコメント\n"
        'const NAME = "search_docs";\n'
        'const DESC = "Find documents — with an em dash.";\n'
        'server.tool(NAME, DESC, { q: z.string().max(5).describe("Query ✓") }, async () => {});\n'
    )
    path = _write(tmp_path, source)
    [tool] = load_typescript(path, parser="tree-sitter").tools
    assert tool.name == "search_docs"
    assert "em dash" in tool.description
    assert [(p.name, p.type_name, p.has_bounds) for p in tool.parameters] == [("q", "string", True)]
    assert tool.parameters[0].description == "Query ✓"
    # The reported line must be the registration's line, not an offset artefact.
    assert tool.line == 4


def test_imported_sink_alias_behaves_identically_in_both_backends(tmp_path: Path) -> None:
    """Parser choice must never change a security finding."""
    source = (
        'import { exec as runShell } from "child_process";\n'
        'server.tool("run", "Run a command.", { cmd: z.string() }, async ({ cmd }) => {\n'
        "  return runShell(cmd);\n"
        "});\n"
    )
    path = _write(tmp_path, source)
    for backend in ("lexical", "tree-sitter"):
        [tool] = load_typescript(path, parser=backend).tools
        assert [s.kind for s in tool.sinks] == ["shell"], backend


def test_registration_inside_a_nested_expression_is_found(tmp_path: Path) -> None:
    source = (
        "function setup(server) {\n"
        '  server.tool("nested", "Registered inside a function.", {}, () => {});\n'
        "}\n"
    )
    extraction = load_typescript(_write(tmp_path, source), parser="tree-sitter")
    assert [t.name for t in extraction.tools] == ["nested"]


def test_syntax_errors_do_not_crash_the_parser(tmp_path: Path) -> None:
    """tree-sitter is error-tolerant; a broken file must degrade, not raise."""
    source = 'const NAME = "a";\nserver.tool(NAME, "d", {}, () => {}) ;;; }}} client.tool('
    extraction = load_typescript(_write(tmp_path, source), parser="tree-sitter")
    assert [t.name for t in extraction.tools] == ["a"]


def test_extract_tools_reports_its_own_backend_name(tmp_path: Path) -> None:
    extraction = extract_tools("", SOURCE, source_file="mem.ts")
    assert extraction.backend == "tree-sitter"


# ------------------------------------------------------------ report integration


def test_json_report_records_the_backend_and_the_unresolved_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Acceptance criterion: coverage is comparable across runs."""
    path = _write(tmp_path, SOURCE)
    assert main(["scan", str(path), "--format", "json", "--fail-on", "never"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["typescript"]["backend"] == "lexical"
    assert payload["typescript"]["unresolved_registrations"] == 5
    # The default backend cannot read this file at all; it says so with an empty tool list
    # rather than a wrong one.
    assert payload["tools"] == []


def test_json_report_records_tree_sitter_when_selected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, SOURCE)
    code = main(
        ["scan", str(path), "--format", "json", "--fail-on", "never", "--ts-parser", "tree-sitter"]
    )
    assert code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["typescript"]["backend"] == "tree-sitter"
    # Three registrations were visible in the syntax but not resolvable.
    assert payload["typescript"]["unresolved_registrations"] == 3
    assert sorted(t["name"] for t in payload["tools"]) == ["read_file", "search_docs"]


def test_a_live_audit_does_not_claim_a_typescript_backend(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """audit-server parses no TypeScript, so the field must be null, not "lexical"."""
    mock = FIXTURES / "mock_mcp_server.py"
    code = main(
        ["audit-server", "--format", "json", "--fail-on", "never", "--", sys.executable, str(mock)]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["typescript"] is None


def test_cli_rejects_an_unknown_backend(tmp_path: Path) -> None:
    path = _write(tmp_path, SOURCE)
    with pytest.raises(SystemExit) as exc:
        main(["scan", str(path), "--ts-parser", "typescript"])
    assert exc.value.code == 2
