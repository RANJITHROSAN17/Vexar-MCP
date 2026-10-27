# Architecture

Vexar-MCP is a four-stage static analysis pipeline. It has zero mandatory runtime dependencies on Python 3.11+ and never executes the code it inspects.

```text
  Target paths (.py / .ts / .js / .jsx / .json)
              │
              ▼
  ┌─────────────────────────┐
  │ 1. Loaders              │  src/mcp_doctor/loaders/
  │  • python_ast.py        │  Parses @mcp.tool(), Tool(...), types, bounds & AST sinks
  │  • typescript_loader.py │  Parses TS/JS MCP SDK server.tool(...) registrations
  │  • json_manifest.py     │  Parses MCP tools/list, OpenAI & Anthropic tool JSON
  └───────────┬─────────────┘
              │  list[ToolSpec]
              ▼
  ┌─────────────────────────┐
  │ 2. Rule Registry        │  src/mcp_doctor/rules/
  │  • contract_rules.py    │  MCP001, MCP008        (tool contract quality)
  │  • token_rules.py       │  MCP002                (per-tool token budget)
  │  • schema_rules.py      │  MCP003, MCP004        (parameter schema drift)
  │  • security_rules.py    │  MCP005, MCP006, MCP007, MCP009, MCP010, MCP013
  └───────────┬─────────────┘
              │  list[Diagnostic]
              ▼
  ┌─────────────────────────┐
  │ 3. Scan Engine          │  src/mcp_doctor/engine.py
  │  • # noqa & TOML filter │  Applies inline & vexar-mcp.toml suppressions
  │  • Health Scorecard     │  Computes 0-100 score, A+..F grade & context token tax
  └───────────┬─────────────┘
              │  ScanReport
              ▼
  ┌─────────────────────────┐
  │ 4. Reporters            │  src/mcp_doctor/reporters/
  │  • terminal.py          │  Human CLI scorecard & token leaderboard
  │  • json_reporter.py     │  CI machine-readable report (dashboard input)
  │  • sarif.py             │  SARIF v2.1.0 for GitHub Code Scanning
  │  • markdown.py          │  GitHub Step Summary & PR comment table
  │  • github_actions.py    │  ::error/::warning/::notice inline PR annotations
  └─────────────────────────┘
```

## Modules

| Path | Responsibility |
| :--- | :--- |
| `models.py` | Frozen dataclasses: `ToolSpec`, `ParameterSpec`, `CodeSink`, `SecretLiteral`, `Diagnostic`, `ScanConfig`, `ScanSummary`, `ScanReport` |
| `engine.py` | File discovery, suppression application, scoring, report assembly |
| `config.py` | TOML discovery (`vexar-mcp.toml`, legacy `.mcp-doctor.toml`, `[tool.vexar-mcp]` in `pyproject.toml`) and CLI overrides |
| `tokens.py` | Deterministic offline token estimation |
| `cli.py` | `scan`, `token-report`, `rules` subcommands and exit-code policy |
| `loaders/` | Source → `ToolSpec` normalization per input format |
| `rules/` | `@register_rule`-decorated check functions plus the registry |
| `reporters/` | `ScanReport` → one of five output formats |
| `simulate/cost_model.py`, `cli_simulate.py` | Experimental cost projection; not wired into the CLI |

## Scoring

`score = max(0, 100 - errors*15 - warnings*6 - infos*2)`; `100` when no tools are found. Grades: `A+` ≥ 97, `A` ≥ 90, `B` ≥ 80, `C` ≥ 70, `D` ≥ 55, else `F`. The formula lives in `engine.py` and is intentionally simple and reviewable.

## Suppression resolution

1. `ignore` in `vexar-mcp.toml` / `pyproject.toml` and `--ignore` on the CLI — removed before rules run.
2. `# noqa: MCP00X[,MCP00Y]` on the diagnostic's source line — removed after rules run, per tool.

## Design principles

1. **Never execute untrusted code during `scan`.** Python targets are inspected with `ast.parse` only.
2. **One rule = one isolated function.** Contributors add rules with `@register_rule(...)` without touching CLI or reporter plumbing.
3. **Deterministic offline token estimation.** Sub-millisecond, no tokenizer blobs, no network.
4. **Reporters are pure functions over `ScanReport`.** Adding a format never touches the engine.
5. **Findings are static signals.** Rule output is a heuristic, not a vulnerability verdict; see [`docs/limitations.md`](docs/limitations.md).

## Extending

- **New rule:** decorate a function in `src/mcp_doctor/rules/`, add positive and negative cases in `tests/test_rules.py`.
- **New input format:** add a loader returning `list[ToolSpec]` and register it in `engine.scan_paths`.
- **New reporter:** pure function `ScanReport -> str`, exported from `reporters/__init__.py`, wired into `cli.py`.

Both workflows are documented in [`CONTRIBUTING.md`](CONTRIBUTING.md).
