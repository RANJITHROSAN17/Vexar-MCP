# GitHub Issue Roadmap Plan

This file is intended to be converted into GitHub issues or grouped milestone issues for the repository.

## Milestone 1: Adoption and baseline workflow

### Issue 1: feat(cli): add `vexar-mcp init` command
Title: `feat(cli): add vexar-mcp init command to scaffold config and CI workflow`

Labels:
- `area/cli`
- `type/feature`
- `roadmap`
- `difficulty/medium`

Summary:
Add a `vexar-mcp init` command that scaffolds a default `vexar-mcp.toml` config and optional GitHub Actions workflow for CI integration.

Acceptance criteria:
- generates default config file with documented settings
- refuses to overwrite existing files unless `--force` is provided
- optionally writes a GitHub Actions workflow
- covers creation and overwrite behavior with tests

### Issue 2: feat(cli): add baseline support for incremental adoption
Title: `feat(cli): add --baseline support for comparing current findings against a saved snapshot`

Labels:
- `area/cli`
- `type/feature`
- `roadmap`
- `difficulty/easy`

Summary:
Allow `vexar-mcp scan` to write and consume a baseline JSON file so CI can fail only on newly introduced findings.

Acceptance criteria:
- supports `--write-baseline` and `--baseline`
- compares findings by stable fingerprint rather than brittle line numbers
- tests cover new, unchanged, and removed findings

## Milestone 2: Coverage and quality improvements

### Issue 3: feat(parsers): add TypeScript MCP loader
Title: `feat(parsers): add TypeScript MCP SDK static loader`

Labels:
- `area/parsers`
- `type/feature`
- `roadmap`
- `difficulty/medium`

Summary:
Support static scanning of `.ts` and `.js` MCP servers built with the official TypeScript MCP SDK.

Acceptance criteria:
- identifies tool definitions and dangerous sinks
- ignores declaration files and node_modules
- fixture-based tests cover vulnerable and clean TypeScript input

### Issue 4: feat(parsers): support YAML manifests and nested schema expansion
Title: `feat(parsers): support YAML manifests and nested typed schemas`

Labels:
- `area/parsers`
- `type/feature`
- `roadmap`
- `difficulty/medium`

Summary:
Expand scanner coverage to include YAML tool manifests and typed nested schemas such as Pydantic and TypedDict models.

Acceptance criteria:
- loads `.yaml` and `.yml` manifests safely with `yaml.safe_load`
- expands nested fields into examined parameter definitions
- tests cover YAML, Pydantic, and TypedDict scenarios

### Issue 5: feat(rules): reduce false positives and improve helper propagation
Title: `feat(rules): improve intra-module helper flow analysis for security rules`

Labels:
- `area/rules`
- `type/performance`
- `roadmap`
- `difficulty/hard`

Summary:
Follow helper functions within the same module so security rules catch dangerous flows when a tool delegates work to private helper functions.

Acceptance criteria:
- detects dangerous sink propagation across local helper calls
- avoids recursion loops
- tests verify attribution of sink results to the originating tool

## Milestone 3: Policy, scoring, and prioritization

### Issue 6: feat(engine): add risk scoring and prioritization
Title: `feat(engine): add deterministic risk scoring and action prioritization`

Labels:
- `area/rules`
- `type/feature`
- `roadmap`
- `difficulty/medium`

Summary:
Add a score that weights severity, exploitability, and mitigations so teams can prioritize what to fix first.

Acceptance criteria:
- scoring is deterministic and documented
- final report includes actionable prioritization
- tests cover known boundaries and mitigation behavior

### Issue 7: feat(policies): add production policy presets
Title: `feat(policies): add strict-prod and enterprise-gov policy presets`

Labels:
- `area/rules`
- `type/feature`
- `roadmap`
- `difficulty/medium`

Summary:
Define reusable policy presets so teams can tune CI enforcement by deployment risk level.

Acceptance criteria:
- supports preset names and explicit overrides
- invalid presets are rejected cleanly
- preset behavior is tested and documented

### Issue 8: feat(rules): add server-wide token warning
Title: `feat(rules): add total-server token budget warning`

Labels:
- `area/rules`
- `type/feature`
- `roadmap`
- `difficulty/easy`

Summary:
Warn when a server as a whole exceeds the configured context budget even if each tool individually passes the per-tool threshold.

Acceptance criteria:
- emits a server-level warning with heavy tool names listed
- config supports maximum server token threshold
- tests cover custom thresholds

## Milestone 4: Runtime validation and live inspection

### Issue 9: feat(cli): add live stdio inspection
Title: `feat(cli): add vexar-mcp inspect --stdio <cmd> for live MCP introspection`

Labels:
- `area/cli`
- `type/feature`
- `roadmap`
- `difficulty/medium`

Summary:
Allow users to run a server over stdio, perform the MCP handshake, and audit the live advertised tools.

Acceptance criteria:
- performs JSON-RPC handshake and `tools/list`
- reuses the existing scan pipeline for live tool definitions
- mock server tests verify handshake and parsing behavior

## Milestone 5: Reporting, dashboards, and governance

### Issue 10: feat(reporters): add HTML dashboard and trend views
Title: `feat(reporters): add self-contained HTML dashboard with trend analytics`

Labels:
- `area/reporters`
- `type/feature`
- `roadmap`
- `difficulty/medium`

Summary:
Create a downloadable or shareable HTML report with health summary, tool cost breakdown, and scan comparisons.

Acceptance criteria:
- valid HTML with inline CSS and safe escaping
- includes filterable diagnostics and summary metrics
- supports historical comparison or trend view

### Issue 11: feat(reporters): add GitHub annotation and CI summary improvements
Title: `feat(reporters): improve GitHub annotations, job summaries, and PR feedback`

Labels:
- `area/reporters`
- `type/feature`
- `roadmap`
- `difficulty/easy`

Summary:
Generate PR-friendly annotations and concise CI summaries so teams can review findings directly from pull requests and actions.

Acceptance criteria:
- `::warning` / `::error` annotations are correctly formatted
- GitHub step summary includes the score and top issues
- CI output remains concise and actionable

## Milestone 6: Launch and product leadership

### Issue 12: docs: publish product positioning and MCP-native market story
Title: `docs: publish product positioning and ecosystem differentiation`

Labels:
- `area/docs`
- `type/feature`
- `roadmap`
- `difficulty/easy`

Summary:
Document why Vexar-MCP is different from generic linters and why it matters specifically for MCP ecosystems.

Acceptance criteria:
- explains use cases, target users, and differentiators clearly
- includes public benchmark examples and feature matrix
- helps new contributors understand the product vision

### Issue 13: release: ship stable v0.2 / v0.3 candidate with benchmark suite
Title: `release: ship Vexar-MCP v0.2/v0.3 with benchmark and ecosystem examples`

Labels:
- `area/ci`
- `type/feature`
- `roadmap`
- `difficulty/medium`

Summary:
Package the product for broader ecosystem trust: versioned release, benchmarks, real examples, and polished docs.

Acceptance criteria:
- stable release includes benchmark examples and documentation
- public examples demonstrate both clean and vulnerable MCP servers
- installation and usage are clear to external users

## Default priority ordering
1. init and baseline
2. TypeScript + YAML + schema support
3. risk score and policy presets
4. live introspection
5. dashboard and trend analytics
6. public launch and benchmark story

This creates a strong product story while avoiding over-expansion too early.
