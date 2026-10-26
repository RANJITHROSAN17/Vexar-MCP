---
title: "Building an Enterprise-Grade MCP Security Scanner with Auto-Bots"
published: true
description: "How I built mcp-doctor — a zero-dependency static analyzer for MCP servers with 7 live bot flows, 100% CI health, and contributor automation."
tags: python, mcp, ai, open-source, github-actions
---

# Building mcp-doctor: From Zero to Enterprise Bot Stack

## The Problem
MCP servers expose security sinks (`shell=True`, SSRF, unconfined paths) and schema drift that standard linters miss. Existing tools either execute untrusted code or ignore AI-agent-specific patterns.

## The Solution: mcp-doctor
- **AST-only analysis** (`ast.parse`) — never runs untrusted code.
- **8 rules** (`MCP001`–`MCP009`) — security + schema + token budget.
- **4 reporters** — terminal, JSON, SARIF, GitHub Markdown.
- **Enterprise bot stack** — 11 workflows governing PRs, roadmap, releases, and contributor onboarding.

## What's New (This Upgrade)
- `MCP009`: Protocol / unbounded pagination detection (Feature 1 from audit)
- `simulate --model`: Token-cost benchmark framework (Feature 2)
- Interactive dashboard (`assets/dashboard.html`) with live audit fetch
- AI Controller Prompt (`.github/prompts/AGENTS.md`) for autonomous improvement

## How the Bot Stack Works
Only 3 notification sources: your merge approval, review failure (only if blocked), weekly issue-raise. No spam.

## Metrics
- 90.77% pytest coverage (`--cov-fail-under=85`)
- 0 `ruff` / `mypy` errors across 21 files
- 100% GitHub Community Health
- 8 contributor issues open (+ 15 queued)

## Try It
```bash
pip install git+https://github.com/RANJITHROSAN17/mcp-doctor.git@main
mcp-doctor audit examples/clean_server.py
```
