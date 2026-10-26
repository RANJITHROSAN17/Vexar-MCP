# mcp-doctor v0.1.0 — Enterprise MCP Security Scanner + Bot Stack

**What:** A static-analysis engine for MCP servers that finds security sinks (`subprocess`, SSRF, path traversal, unsafe deserialization) and schema drift — without executing untrusted code.

**New in this release:**
- `MCP009` rule (protocol / legacy transport & unbounded pagination detection)
- `simulate --model` token-cost benchmark framework
- 11 GitHub Actions bot workflows (Feature Upgrade, Issue Raise, Issue Resolve, Code Review, Final Merge, Welcome, Stale + CI / Governance / Release)
- Premium interactive dashboard (`assets/dashboard.html`) — live scorecard, violation feed, 7-flow status
- `feature_request.yml` + `AI_CONTROLLER_PROMPT.md` for autonomous repo management

**Try it:**
```bash
pip install git+https://github.com/RANJITHROSAN17/mcp-doctor.git@main
mcp-doctor audit examples/clean_server.py
```

**Contribute:** 8 open contributor issues (`good first issue` / `help wanted`) — `#1`–`#8`. Zero experience needed for rules; medium difficulty for TypeScript parser + CLI init.

**Links:**
- Repo: https://github.com/RANJITHROSAN17/mcp-doctor
- Dashboard: https://ranjithrosan17.github.io/mcp-doctor/ (after Pages enabled)
- Release: https://github.com/RANJITHROSAN17/mcp-doctor/releases/tag/v0.1.0
- Rules: `src/mcp_doctor/rules/` (MCP001–MCP009)
