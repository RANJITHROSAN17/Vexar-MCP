# Vexar-MCP v0.2.3 — security and governance checks for MCP servers

> **Draft.** Not published. Every number below was measured against `main` at the time of
> writing; re-run the commands before posting, because the tool's own output is the source of
> truth.

**What:** a static analyzer and live auditor for Model Context Protocol servers. It reads
Python, TypeScript/JavaScript and JSON tool definitions without executing them, and reports
security sinks, schema drift, context-token cost, and protocol conformance — then hands the
result to a policy gate that can fail a build.

**In this release:**

- **15 rules** (`MCP001`–`MCP015`) across security, schema, contract and token categories.
  Fourteen run on static scans; `MCP012` runs under `audit-server`, because protocol
  conformance can only be observed on a live connection.
- **Six report formats** — terminal, JSON, SARIF v2.1.0, GitHub Markdown, GitHub Actions
  annotations for inline PR feedback, and a standalone HTML dashboard. `--emit` renders
  several from a single scan.
- **Seven commands** — `scan`, `audit-server`, `baseline`, `policy`, `rules`, `token-report`,
  `simulate`.
- **Protocol-revision aware.** Live audits negotiate `2026-07-28` (stateless), `2025-11-25`,
  `2025-06-18`, `2025-03-26` and `2024-11-05` (handshake). An unsupported revision is a
  finding, never a silent pass.
- **Honest scoring.** A grade is withheld (`n/a`) when part of the codebase could not be
  parsed, rather than reporting an A+ over an unread file.
- **Zero runtime dependencies** on Python 3.10+; no network calls at scan time.

**Try it:**

```bash
pip install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@main"
vexar-mcp scan examples/vulnerable_server.py
# 12 findings across 2 tools -> Health Score 4/100 (F), Risk Index 98/100 (critical)

vexar-mcp scan examples/clean_server.py examples/tools_manifest.json
# 100/100 (A+)
```

**Contribute:** contributor-ready tasks are labelled
[`good first issue`](https://github.com/RANJITHROSAN17/Vexar-MCP/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
and [`help wanted`](https://github.com/RANJITHROSAN17/Vexar-MCP/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22).
Adding a rule is a decorated function plus its tests — [issue #5](https://github.com/RANJITHROSAN17/Vexar-MCP/issues/5)
was done exactly that way.

**Links:**

- Repository: https://github.com/RANJITHROSAN17/Vexar-MCP
- Releases: https://github.com/RANJITHROSAN17/Vexar-MCP/releases
- Rules: [`docs/rules.md`](../docs/rules.md)
- Usage: [`docs/usage.md`](../docs/usage.md)
- Roadmap and governance: [`.github/roadmap.json`](../.github/roadmap.json), [`docs/governance.md`](../docs/governance.md)

**Honest caveats:** no PyPI release exists yet — install from the repository (this is tracked as
`RM-023` and is the one release gap in [`docs/review-2026-10.md`](../docs/review-2026-10.md)).
Findings are static heuristics, not confirmed vulnerabilities; see
[`docs/limitations.md`](../docs/limitations.md). `info` findings still cost two health-score
points, so a well-built server with several undocumented parameters grades `A` rather than `A+`.
