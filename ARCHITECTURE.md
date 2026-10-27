# Architecture Overview

`mcp-doctor` is structured as a clean four-stage static analysis pipeline with zero mandatory runtime dependencies on Python 3.11+:

```text
  Target Paths (.py / .json)
              │
              ▼
  ┌─────────────────────────┐
  │ 1. Loaders              │  src/mcp_doctor/loaders/
  │  • python_ast.py        │  Parses @mcp.tool(), Tool(...), types, bounds & AST sinks
  │  • json_manifest.py     │  Parses MCP tools/list, OpenAI & Anthropic tool JSONs
  └───────────┬─────────────┘
              │  list[ToolSpec]
              ▼
  ┌─────────────────────────┐
  │ 2. Rule Registry        │  src/mcp_doctor/rules/
  │  • contract_rules.py    │  MCP001, MCP008
  │  • token_rules.py       │  MCP002
  │  • schema_rules.py      │  MCP003, MCP004
  │  • security_rules.py    │  MCP005, MCP006, MCP007
  └───────────┬─────────────┘
              │  list[Diagnostic]
              ▼
  ┌─────────────────────────┐
  │ 3. Scan Engine          │  src/mcp_doctor/engine.py
  │  • # noqa & TOML filter │  Applies inline & .mcp-doctor.toml suppressions
  │  • Health Scorecard     │  Computes 0-100 score, A+..F grade & context token tax
  └───────────┬─────────────┘
              │  ScanReport
              ▼
  ┌─────────────────────────┐
  │ 4. Reporters            │  src/mcp_doctor/reporters/
  │  • terminal.py          │  Human CLI scorecard & token leaderboard
  │  • json_reporter.py     │  CI machine-readable report
  │  • sarif.py             │  SARIF v2.1.0 for GitHub Security tab
  │  • markdown.py          │  GitHub Step Summary & PR comment table
  │  • github_actions.py    │  ::error/::warning/::notice inline PR annotations
  └─────────────────────────┘
```

## Design Principles

1. **Never execute untrusted code during `scan`**: Python servers are inspected purely via `ast.parse`.
2. **One rule = one isolated function**: Contributors can add rules via `@register_rule(...)` without touching CLI or reporter plumbing.
3. **Deterministic offline token estimation**: Context-window token estimates run in sub-millisecond time without downloading external tokenizer blobs.
