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

---

## Platform layer (0.2.0)

The 0.1 pipeline was `loaders → rules → engine → reporters`. The platform layer wraps that
core without diluting it: rules still only see normalized tools, and reporters still only
see a `ScanReport`.

```
                       ┌──────────────────────────────────────────┐
   files ──loaders──►  │              rule catalog                │
                       │  built-in (MCP001-MCP011, MCP013-MCP015) │
   live server         │  + opt-in rule packs (rules/plugin.py)   │
   ──live.py────►      └───────────────┬──────────────────────────┘
   (stdio JSON-RPC)                    │  tools + diagnostics
                                       ▼
                        engine.assemble_report()
                        ├── inline suppression filtering
                        ├── risk.apply_risk()      → risk per finding + risk_index
                        └── score / grade / summary
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              reporters          baseline.compare()   gate evaluation
        terminal/json/sarif/     (delta: new vs accepted)  (policy presets,
        markdown/github/html                              min score, fail-on)
```

### Module responsibilities

| Module | Responsibility | Does not |
| :--- | :--- | :--- |
| `live.py` | Launch a server, speak JSON-RPC, normalize responses | Decide policy or format output |
| `policies.py` | Named presets, precedence, protected-rule invariant | Detect anything |
| `baseline.py` | Fingerprints, snapshots, delta comparison | Mutate a report |
| `risk.py` | Deterministic per-finding risk and aggregate index | Know about rules' internals |
| `rules/plugin.py` | Load and validate opt-in packs | Load anything implicitly |
| `engine.py` | Orchestration and the single report-shaping path | Read files itself (loaders do) |
| `config.py` | Resolve files, presets, and CLI overrides into one `ScanConfig` | Enforce gates |
| `cli.py` | Argparse, dispatch, exit codes | Contain detection logic |

### Invariants worth preserving

1. **One report path.** Static scans and live audits both end in
   `engine.assemble_report()`, so scores, grades, and risk are comparable across modes.
2. **Rules stay pure.** A rule receives `(tools, config)` and returns diagnostics. No I/O,
   no network, no filesystem — which is what keeps the rule set testable and safe.
3. **Fail closed on configuration.** Unknown keys, invalid enums, unknown rule IDs, and
   malformed baselines are errors (exit `2`), never silently ignored.
4. **A policy cannot be quietly weakened.** Protected categories are enforced at
   configuration resolution time, in one place (`policies.apply_policy`).
5. **Nothing is executed unless the operator asked for it.** Static scans never import
   scanned code; the two features that run code (`audit-server`, Python rule packs) are
   explicit, opt-in, and documented as trust boundaries.
6. **Exit codes are API.** `0` pass, `1` gate failure, `2` usage/config/IO error.
