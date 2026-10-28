# Vexar-MCP Product Roadmap

## Product thesis

Vexar-MCP should evolve from a useful MCP security scanner into a security and governance platform for Model Context Protocol servers and AI tool integrations.

The current repository is a strong v1 foundation: it already includes a rule engine, CLI, CI integration, structured reports, and a clear architecture. The next step is not to keep adding more one-off rule checks. The next step is to turn this into a platform that helps teams adopt, govern, and trust MCP tooling in production.

## Core product direction

The product should be built around five pillars:

1. Security scanning
   - detect risky patterns in tool definitions and handlers
   - enforce safe coding practices for MCP tools
2. Schema quality validation
   - ensure tools are well-described, typed, and usable by agents
3. Token-cost awareness
   - minimize context-window bloat and tool cost explosion
4. Governance and policy enforcement
   - allow teams to define and enforce standards consistently
5. Live and runtime auditing
   - inspect actual MCP servers and protocol behaviors, not only source code

This is the real product story: not “a linter,” but “the control plane for safe MCP adoption.”

## Strategic goals

### Primary goal
Make MCP servers safer, more governable, and more production-ready for both developer teams and enterprise AI platforms.

### Supporting goals
- reduce insecure tool design in the wild
- make security and contract quality visible in CI and PRs
- help teams enforce organizational standards at scale
- reduce confusion caused by ambiguous, oversized, or poorly documented tool schemas
- make MCP security measurable over time

## Product principles

1. Start with a clear problem
   - MCP servers currently ship without standard security and contract guardrails.
2. Maintain a strong rule engine
   - rule quality and deterministic behavior are the foundation.
3. Keep architecture modular
   - loaders, rules, engine, and reporters remain decoupled.
4. Validate against real usage patterns
   - test against real tool manifests and real server structures.
5. Add CI and policy layers early
   - the tool only becomes valuable when it blocks insecure patterns before release.
6. Productize after reliability
   - packaging, policy layers, and governance come before broad feature sprawl.

## Recommended product evolution

### Phase 1: Product hardening and release readiness
Focus: make the project stable, trustworthy, and production-ready.

Planned work:
- stable PyPI release and versioned CLI
- semantic versioning and changelog discipline
- release notes and migration policy
- clearer config precedence (`vexar-mcp.toml`, CLI flags, pyproject config)
- support matrix for Python versions and install methods
- improved onboarding flow for new users

Why this matters:
Teams will not adopt a security tool without predictable releases, clear configuration, and trust in the upgrade path.

Deliverables:
- v1.0 release candidate
- installation guide for Docker, pip, and git-based installs
- stable configuration schema
- deprecation policy and compatibility guarantees

### Phase 2: Governance and policy engine
Focus: convert rules into enforceable standards.

Planned work:
- `strict-prod` preset
- `enterprise-governance` preset
- baseline comparison support
- `new findings only` CI mode
- team-specific policy profiles
- allowlist / denylist and suppression policy
- policy inheritance and override rules

Why this matters:
Security products win when they become policy infrastructure, not just static checkers.

Deliverables:
- policy configuration model
- reusable profiles for teams and orgs
- CI enforcement examples across GitHub, GitLab, and Azure DevOps
- baseline workflow for large existing MCP deployments

### Phase 3: Live auditing and runtime validation
Focus: audit what is actually running, not just what is typed into source files.

Planned work:
- stdio JSON-RPC introspection for live MCP servers
- `tools/list` validation and protocol checks
- transport validation
- server startup safety checks
- runtime metadata auditing for deployed MCP servers
- compatibility checks across standard MCP frameworks

Why this matters:
Live validation catches dynamic registration, transport mismatches, or runtime metadata drift that static analysis cannot see.

Deliverables:
- `vexar-mcp inspect --stdio ...` maturity
- runtime protocol validation module
- live audit workflow in CI and staging environments

### Phase 4: Better UX and reporting
Focus: help teams understand results quickly and act on them.

Planned work:
- interactive HTML dashboard
- historical trend analytics
- severity and rule filtering
- per-tool and per-server cost summaries
- shareable reports for engineering leadership and security review
- GitHub PR summary and inline annotation improvements

Why this matters:
Teams need dashboards and trendlines, not just raw terminal output.

Deliverables:
- HTML report with readable scorecards
- trend view across scan runs
- filtered findings and drill-down views

### Phase 5: Platform extensibility
Focus: create a trusted extension model for advanced teams.

Planned work:
- custom rule pack loading
- signed / opt-in community rule packs
- explicit plugin trust boundaries
- governance for third-party extensions
- compliance templates and policy bundles

Why this matters:
This moves the project from a single-tool scanner into a platform ecosystem without compromising safety.

Deliverables:
- plugin registry design
- third-party extension trust model
- custom policy bundles for enterprise environments

## Immediate priorities for the next milestone

If the next milestone is to make Vexar-MCP a real product, prioritize these items in order:

1. Stable release packaging
   - PyPI publication
   - stable versioning
   - clear upgrade path
2. Baseline support
   - compare current and historical scans
   - support incremental adoption without noisy failures
3. Policy presets
   - strict-prod and enterprise defaults
4. Live server inspection
   - inspect running MCP servers over stdio / JSON-RPC
5. Dashboard and historical reporting
   - trend lines, scorecards, drill-down views
6. Plugin trust model
   - safe extension path for custom compliance and rule packs

## Product positioning

The best positioning is not “MCP security scanner.”

Use this framing:
- Security and governance platform for MCP servers
- Static + live analysis for MCP tool ecosystems
- Policy engine for safe AI tool deployment
- SAST for Model Context Protocol environments

This is much easier to explain to customers, enterprise buyers, and contributors.

## Target users

Primary users:
- AI platform teams
- MCP server authors
- security engineers auditing tool integrations
- enterprise AI teams with governance requirements
- teams shipping MCP tools in CI and PR workflows

Secondary users:
- open-source maintainers
- plugin ecosystem authors
- compliance and risk review teams

## Success metrics

The product should be considered successful when it achieves the following:

- easy installation and onboarding for new users
- stable release velocity without breaking config expectations
- strong adoption in GitHub Actions / CI workflows
- policy-based enforcement in teams and orgs
- live runtime validation for production MCP deployments
- a clear story for enterprise governance and auditability

## Anti-goals

Avoid getting stuck in these traps:
- adding rules without product clarity
- treating the project as a rules-only playground
- building features without clear user value
- sacrificing trust for velocity
- becoming a tool that is hard to install, configure, or enforce

## Recommended strategy for this repo

The right next step is to keep the architecture clean while expanding the product surface in layers:

- Layer 1: reliability and packaging
- Layer 2: policy and governance
- Layer 3: live environment validation
- Layer 4: reporting and analytics
- Layer 5: extensibility and ecosystem support

This gives the project a clear evolution path from strong technical foundation to real platform product.

## Final recommendation

Treat the current repository as a strong v1 foundation and evolve it deliberately into an MCP security and governance platform.

The product should not just detect bad patterns. It should help teams:
- trust what they ship,
- enforce standards in CI,
- understand risk over time,
- validate MCP servers realistically,
- and scale secure AI-tool adoption safely.

That is the best long-term product direction for Vexar-MCP.
