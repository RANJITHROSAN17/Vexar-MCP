---
title: "Static analysis for MCP servers: finding risky patterns before they ship"
published: false
description: "How Vexar-MCP checks MCP servers for dangerous Python patterns, schema drift, and context-token cost — without executing the code it inspects."
tags: python, mcp, ai, open-source, github-actions
---

> **Draft.** Not published. Re-verify every number below against the target commit before posting.

# Vexar-MCP: static analysis for MCP servers

## The problem

MCP servers hand a model a set of tools and the authority to call them. Two failure surfaces follow:

1. **Tool handlers reach privileged operations.** `subprocess.run(cmd, shell=True)`, an unvalidated `urlopen`, a path built from model input.
2. **Tool definitions are expensive and vague.** Every description and schema is re-sent on every turn, and a tool called `run` with an untyped `Any` parameter invites the model to guess.

Standard linters do not model these patterns, and dynamic scanners have to run the server — which is exactly what you do not want to do with untrusted code in CI.

## The approach

Vexar-MCP parses Python with `ast.parse` and reads JSON manifests with the standard library. The scanned file is never imported, so a scan cannot execute attacker-controlled code. Analysis is offline and has no runtime dependencies on Python 3.11+.

Eleven rules map to the two surfaces:

- **Security:** shell execution, SSRF sinks, unconfined filesystem access, hardcoded credentials, missing network timeouts, unsafe deserialization.
- **Contract and schema:** missing tool descriptions, generic or duplicate names, untyped parameters, unbounded pagination.
- **Tokens:** per-tool context budget.

## What you get back

Five output formats from the same report object: terminal scorecard, JSON, SARIF v2.1.0, GitHub Markdown for the job summary, and GitHub Actions workflow commands for inline PR annotations.

```console
$ vexar-mcp scan examples/vulnerable_server.py
==============================================================================
Vexar-MCP  |  Health Score: 8/100 (F)  |  Tools: 2 across 1 file(s)
Context Footprint: ~105 tokens  |  Errors: 4  Warnings: 5  Info: 1
==============================================================================

[ERROR] MCP005 (dangerous-shell-execution) at examples/vulnerable_server.py:25 [run]
        Tool 'run' invokes dangerous execution sink `subprocess.run(shell=True)` with parameter 'cmd'.
        Fix: Use `subprocess.run([cmd, arg], shell=False, check=True)` with validated arguments.
```

## What it deliberately does not do

It is not a security review. It does not trace data flow across modules, it cannot see tools registered dynamically at runtime, and its token counts are a fast offline estimate rather than a tokenizer round-trip. False positives happen; every finding is actionable and suppressible.

## Try it

```bash
pip install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@main"
vexar-mcp scan path/to/your_mcp_server
```

Repository: https://github.com/RANJITHROSAN17/Vexar-MCP
