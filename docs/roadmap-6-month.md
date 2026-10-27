# Vexar-MCP 6-Month Roadmap

## Objective
Turn Vexar-MCP from a strong early-stage security scanner into a credible, trusted platform for MCP auditing, governance, and operational risk management.

## Quarter 1 — Trust, adoption, and core maturity

### Milestone 1: Developer onboarding and adoption foundation
Target: month 1

Focus:
- `vexar-mcp init` command
- default config generation (`vexar-mcp.toml`)
- baseline support (`--baseline`, `--write-baseline`)
- clearer install docs and quick start
- more explicit docs on what Vexar catches and does not catch

Why this matters:
- adoption is blocked if users cannot onboard quickly
- teams need baseline workflows to adopt CI without historical churn

Deliverables:
- CLI init flow
- config file generation and docs
- baseline compare workflow
- improved README / docs landing flow

Acceptance criteria:
- user can install and scan a server in under 10 minutes
- repo can adopt Vexar in CI without immediately fixing all historical findings
- onboarding docs validated by real users

### Milestone 2: Coverage and reliability expansion
Target: month 2

Focus:
- TypeScript/JavaScript MCP loader
- YAML manifest support
- Pydantic/TypedDict schema expansion
- helper-function propagation and false-positive reduction
- rule precision improvements

Why this matters:
- real-world MCP servers are multi-language and multi-format
- static analysis becomes much more useful when it handles common real-world construction patterns

Deliverables:
- TS/JS scanner support
- YAML tool manifest support
- improved nested schema handling
- reduce false positives in tool flow analysis

Acceptance criteria:
- supports common Python and TypeScript MCP server patterns
- works on json/yaml/manifests without requiring runtime execution
- rule precision noticeably improves on real-world repo fixtures

### Milestone 3: Policy and risk prioritization
Target: month 3

Focus:
- risk scoring model
- policy presets (`strict-prod`, `enterprise-gov`, `default`)
- server-wide token budget warnings
- rule severity weights and remediation prioritization

Why this matters:
- teams do not want raw warnings; they want actionable priorities
- product value increases when Vexar becomes a governance tool, not just a linter

Deliverables:
- risk score framework
- policy presets
- priority summary in reports
- better assessment of what should fail CI first

Acceptance criteria:
- users can tell which tool issues are worth fixing first
- CI can enforce policy presets consistently
- scoring remains deterministic and understandable

## Quarter 2 — Differentiation, runtime auditing, and enterprise readiness

### Milestone 4: Live MCP introspection and runtime validation
Target: month 4

Focus:
- `vexar-mcp inspect --stdio <cmd>`
- runtime tool introspection over MCP JSON-RPC
- static + dynamic audit combination
- support for live tool schema validation

Why this matters:
- static analysis alone cannot cover all runtime behaviors
- live inspection is a major product differentiator and increases trust in the tool

Deliverables:
- stdio server introspection workflow
- live tool list evaluation against Vexar rules
- runtime comparison of declared vs actual tool schema

Acceptance criteria:
- can inspect a running MCP server without needing custom runtime setup
- reports connect live tool definitions to audit rules
- dynamic behavior is visible to users without writing custom scripts

### Milestone 5: Dashboard, trend analytics, and evidence-based review
Target: month 5

Focus:
- HTML dashboard improvements
- current vs previous scan comparison
- trend analytics and historical snapshots
- team-friendly risk summary pages
- shareable reports in CI artifacts or GitHub pages

Why this matters:
- reports become more actionable for teams and leadership
- Vexar becomes more than a one-off scan tool

Deliverables:
- comparison dashboard views
- trend tracking across versions
- improved HTML / report UX
- shareable operational summaries

Acceptance criteria:
- leadership and engineering teams can review risk trends effortlessly
- new findings vs resolved findings are visible
- dashboard is self-contained, no vendor dependency

### Milestone 6: Product launch and ecosystem leadership
Target: month 6

Focus:
- polished release plan
- benchmark examples and validation suite
- public comparison strategy
- enterprise docs and adoption case studies
- roadmap presentation for contributors and maintainers

Why this matters:
- Vexar needs a clear identity in the AI security ecosystem
- the product must look like a durable platform, not a hobby project

Deliverables:
- release v0.2 / v0.3 candidate
- benchmark examples for clean vs vulnerable MCP servers
- public product positioning page
- comparison doc against generic scanners and static checkers

Acceptance criteria:
- public docs clearly explain why Vexar is MCP-native
- product is demonstrably easier to adopt than generic tooling for MCP use cases
- community sees a clear path to contribution and product maturity

## Key outcomes after 6 months
By the end of the 6-month window, Vexar-MCP should be able to claim:
- strong support for Python and TypeScript MCP servers
- policy-aware CI scanning
- trustable risk scoring
- live MCP introspection
- baseline and regression monitoring
- clear enterprise governance story

That position will make it substantially stronger than a generic scanner and capable of competing at the top of the MCP tool ecosystem.

## Strategic recommendation
The highest-leverage sequence is:
1. adoption and baseline tooling
2. cross-language coverage and rule quality
3. risk and policy features
4. live inspection and unified reporting
5. dashboard + trend analytics
6. ecosystem positioning

This path builds real product trust before scope expansion.
