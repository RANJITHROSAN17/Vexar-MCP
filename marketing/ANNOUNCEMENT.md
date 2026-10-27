# Vexar-MCP v0.1.0 — security and quality checks for MCP servers

> **Draft.** Not published. Re-verify every number and link below against the branch you announce before posting.

**What:** a static analyzer for Model Context Protocol servers. It reads Python, TypeScript/JavaScript and JSON tool definitions without executing them, and reports security sinks, schema drift, and context-token cost.

**In this release:**

- **11 rules** (`MCP001`–`MCP010`, `MCP013`) covering shell execution, SSRF, path traversal, hardcoded credentials, missing timeouts, unsafe deserialization, tool description quality, parameter typing, unbounded pagination, token budget, and tool naming.
- **5 report formats** — terminal, JSON, SARIF v2.1.0, GitHub Markdown, and GitHub Actions annotations for inline PR feedback.
- **Zero runtime dependencies** on Python 3.11+; no network calls at scan time.
- **Composite GitHub Action** exposing `score`, `grade`, and `total-diagnostics` as step outputs.

**Try it:**

```bash
pip install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@main"
vexar-mcp scan examples/vulnerable_server.py    # 10 findings across 2 tools
vexar-mcp scan examples/clean_server.py examples/tools_manifest.json   # 100/100 (A+)
```

**Contribute:** contributor-ready tasks are labelled [`good first issue`](https://github.com/RANJITHROSAN17/Vexar-MCP/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) and [`help wanted`](https://github.com/RANJITHROSAN17/Vexar-MCP/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22). Adding a rule is a decorated function plus two tests.

**Links:**

- Repository: https://github.com/RANJITHROSAN17/Vexar-MCP
- Release: https://github.com/RANJITHROSAN17/Vexar-MCP/releases/tag/v0.1.0
- Rules: [`docs/rules.md`](../docs/rules.md)
- Usage: [`docs/usage.md`](../docs/usage.md)

**Honest caveats:** no PyPI release exists yet — install from the repository. Findings are static heuristics, not confirmed vulnerabilities; see [`docs/limitations.md`](../docs/limitations.md).
