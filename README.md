<p align="center">
  <img src="assets/banner.svg" alt="Vexar-MCP — security and quality checks for MCP servers" width="100%" style="max-width:1100px">
</p>

<h1 align="center">Vexar-MCP</h1>

<p align="center">
  <strong>Security and quality checks for Model Context Protocol servers.</strong><br>
  Find risky Python patterns, validate tool schemas, and see what your tool definitions cost in context tokens — before you ship.
</p>

<p align="center">
  <a href="https://github.com/RANJITHROSAN17/Vexar-MCP/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/RANJITHROSAN17/Vexar-MCP/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/RANJITHROSAN17/Vexar-MCP/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/RANJITHROSAN17/Vexar-MCP?color=2dd4bf"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-3776ab">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-111827"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#see-it-work">Example output</a> ·
  <a href="#what-it-checks">Rules</a> ·
  <a href="docs/usage.md">Usage</a> ·
  <a href="docs/scan-status.md">Scan status</a> ·
  <a href="docs/policies.md">Policies</a> ·
  <a href="docs/baseline.md">Baselines</a> ·
  <a href="docs/live-audit.md">Live audit</a> ·
  <a href="docs/github-actions.md">GitHub Action</a>
</p>

---

Vexar-MCP analyzes MCP servers and enforces a policy over them. Static scans read Python source with `ast.parse` and JSON tool manifests with the standard library — **your code is never imported or executed**. It also tells you the difference between "this code is clean" and "this code could not be read": every report separates **findings** from **analysis status and coverage**, and a scan that could not parse your files is never graded. See [scan status](docs/scan-status.md). `audit-server` goes further and introspects a *running* server over stdio JSON-RPC, because generated and feature-flagged tools are invisible to static analysis.

Findings are reported as shell output, JSON, SARIF, GitHub Markdown, inline Pull Request annotations, or a standalone HTML dashboard — and gated by a named policy so the same standard applies on a laptop and in CI.

It does not replace a security review. It catches a specific set of well-known MCP failure modes early, and it makes the standard you enforce explicit and reviewable. [What it cannot do](docs/limitations.md) is documented explicitly.

---

## Quick start

**Requirements:** Python 3.10+. No runtime dependencies on Python 3.11+.

```bash
# 1. Install (from source — there is no PyPI release of this package yet)
pip install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@main"

# 2. Scan a server, a directory, or a tool manifest
vexar-mcp scan path/to/your_mcp_server

# 3. Enforce a policy instead of ad-hoc flags
vexar-mcp scan . --policy strict-prod

# 4. Adopt on an existing codebase without failing on day one
vexar-mcp scan . --write-baseline .vexar-baseline.json
vexar-mcp scan . --baseline .vexar-baseline.json --policy strict-prod

# 5. Audit a running server over stdio JSON-RPC
vexar-mcp audit-server -- python my_server.py

# 6. Get a machine-readable report for CI, or a self-contained HTML dashboard
vexar-mcp scan . --format sarif --output vexar.sarif
vexar-mcp scan . --format html  --output vexar-report.html
```

Try it against the bundled examples without installing anything into your project:

```bash
git clone https://github.com/RANJITHROSAN17/Vexar-MCP.git && cd Vexar-MCP
python -m venv .venv && . .venv/bin/activate && pip install -e .
vexar-mcp scan examples/vulnerable_server.py    # 10 findings, exit code 1
vexar-mcp scan examples/clean_server.py examples/tools_manifest.json   # 100/100, exit code 0
```

<details>
<summary>Other installation methods</summary>

```bash
# Isolated CLI install
pipx install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@main"

# Editable install for contributing
git clone https://github.com/RANJITHROSAN17/Vexar-MCP.git && cd Vexar-MCP
pip install -e ".[dev]"

# Pre-commit hook
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/RANJITHROSAN17/Vexar-MCP
    rev: main
    hooks:
      - id: vexar-mcp
```

</details>

---

## See it work

Real output from `vexar-mcp scan examples/vulnerable_server.py`, run against the intentionally flawed example in this repository (`...` marks findings elided for length):

```console
$ vexar-mcp scan examples/vulnerable_server.py
==============================================================================
Vexar-MCP  |  Health Score: 8/100 (F)  |  Tools: 2 across 1 file(s)
Context Footprint: ~105 tokens  |  Errors: 4  Warnings: 5  Info: 1
==============================================================================

[ERROR] MCP001 (missing-tool-description) at examples/vulnerable_server.py:23 [run]
        Tool 'run' has an empty or overly brief description (0 chars; minimum 12 recommended).
        Fix: Document purpose, inputs, and side effects in the tool docstring or `description`.
[ERROR] MCP008 (ambiguous-or-duplicate-tool-name) at examples/vulnerable_server.py:23 [run]
        Tool name 'run' is overly generic or violates `[a-z][a-z0-9_-]{2,63}`.
        Fix: Use specific `verb_noun` `snake_case` naming such as `fetch_invoice_summary`.
[WARN ] MCP003 (untyped-or-any-parameter) at examples/vulnerable_server.py:23 [run]
        Parameter 'cmd' in tool 'run' has no type annotation.
        Fix: Provide an explicit scalar, `Literal[...]`, `TypedDict`, or Pydantic `BaseModel` type.
...
[ERROR] MCP005 (dangerous-shell-execution) at examples/vulnerable_server.py:25 [run]
        Tool 'run' invokes dangerous execution sink `subprocess.run(shell=True)` with parameter 'cmd'.
        Fix: Use `subprocess.run([cmd, arg], shell=False, check=True)` with validated arguments.
```

Every finding gives you four things: a **stable rule ID** (`MCP005`) you can suppress or track, the **exact file and line**, a **category and severity**, and a **concrete fix** — from the same data that drives JSON, SARIF, Markdown and PR annotations.

A reference implementation that passes every rule, plus a bounded JSON manifest, scores 100:

```console
$ vexar-mcp scan examples/clean_server.py examples/tools_manifest.json
==============================================================================
Vexar-MCP  |  Health Score: 100/100 (A+)  |  Tools: 3 across 2 file(s)
Context Footprint: ~425 tokens  |  Errors: 0  Warnings: 0  Info: 0
==============================================================================
All checks passed! Zero security, schema, contract, or token-budget findings.
```

---

## What it checks

Fourteen rules across four categories — thirteen static, plus `MCP012` for live protocol validation. Run `vexar-mcp rules` to list them, or `vexar-mcp rules --format json` for the machine-readable catalog.

| Rule | Severity | Category | Flags |
| :--- | :--- | :--- | :--- |
| `MCP001` | error | contract | Tool has no meaningful description (`< 12` chars), which degrades tool selection |
| `MCP002` | warning | tokens | A single tool definition exceeds the per-tool token budget (default `250`) |
| `MCP003` | warning | schema | Parameter has no type annotation, or uses unconstrained `Any` / bare `dict` |
| `MCP004` | info | schema | Pagination / `limit` / batch parameter has no upper bound |
| `MCP005` | error | security | `shell=True`, `os.system`, `eval`, `exec` reached from a tool handler |
| `MCP006` | warning | security | Tool input flows into an outbound HTTP call without visible host/scheme validation |
| `MCP007` | warning | security | Tool input flows into a filesystem path without base-directory confinement |
| `MCP008` | error | contract | Duplicate, generic, or non-conforming tool name |
| `MCP009` | error | security | Hardcoded credential literal inside a tool handler (provider token prefixes, bearer headers) |
| `MCP010` | warning | security | Outbound HTTP call with no explicit `timeout=` |
| `MCP011` | error | security | SQL statement built by interpolating tool input into `.execute(...)` / `.executemany(...)` |
| `MCP012` | warning | contract | Live audit only: unsupported protocol revision, missing `tools` capability, malformed `tools/list` entries, runaway pagination |
| `MCP013` | error | security | `pickle.loads`, `yaml.load` without `SafeLoader`, and similar unsafe deserialization |
| `MCP014` | warning | tokens | The whole tool catalogue exceeds the server-wide token budget, with its largest contributors named |

Rules are static pattern and data-flow heuristics, not a proof of exploitability — [read the limitations](docs/limitations.md) before treating a finding as a confirmed vulnerability. Per-rule detail and remediation examples: [`docs/rules.md`](docs/rules.md).

**Supported inputs**

| Input | What is parsed |
| :--- | :--- |
| `.py` | `@mcp.tool()` / `@server.tool()` decorators, `Tool(...)` objects, `FastMCP` builders, parameter types, defaults, bounds, and sink calls in the handler body |
| `.ts` / `.js` / `.jsx` | TypeScript MCP SDK `server.tool(...)` registrations, parsed statically without a Node runtime |
| `.json` | MCP `tools/list` payloads, OpenAI and Anthropic tool manifests |

---

## Report formats

Add a format to `vexar-mcp scan .`:

| Format | Flag | Intended use |
| :--- | :--- | :--- |
| Terminal | *(default)* | Local development, scorecard and diagnostics |
| JSON | `--format json` | Your own tooling, dashboards, artifact storage |
| SARIF | `--format sarif` | GitHub Code Scanning, any SARIF-consuming IDE |
| Markdown | `--format markdown` | `$GITHUB_STEP_SUMMARY`, PR comments |
| Annotations | `--format github` | Inline `::error` / `::warning` / `::notice` on PR diff lines — no SARIF upload or Advanced Security needed |
| HTML | `--format html --output report.html` | One self-contained file: scorecards, filters, search, tool costs, trend sparkline. No CDN, works offline |

Plus `vexar-mcp simulate . --model claude-sonnet-4.5 --calls-per-day 2000` to turn a
catalog into a monthly bill projection, and `vexar-mcp token-report .` for a per-tool
context-window leaderboard:

```console
$ vexar-mcp token-report examples/clean_server.py examples/tools_manifest.json
TOOL NAME                         EST. TOKENS   PARAMS  SOURCE
------------------------------------------------------------------------------
list_kubeflow_runs                        186        2  examples/tools_manifest.json:1
read_workspace_note                       142        2  examples/clean_server.py:24
check_package_metadata                     97        1  examples/clean_server.py:41
------------------------------------------------------------------------------
Total context-window tax across 3 tool(s): ~425 tokens/turn
```

Token counts are a deterministic offline estimate (character- and structure-based), not a tokenizer round-trip; see [`docs/limitations.md`](docs/limitations.md).


---

## Trustworthy results: status, coverage, exit codes

A passing gate must mean "we read your code and it is fine", not "we found nothing to read".
Every report therefore carries a status and five coverage counters alongside the findings:

```console
$ vexar-mcp scan src/servers/
Vexar-MCP  |  Health Score: n/a (nothing to grade)  |  Tools: 2 across 1 file(s)
Policy: default  |  Risk Index: 0/100 (none)  |  Status: PARTIAL
Coverage: 2 candidate file(s), 1 analyzed, 1 failed, 1 with tools
==============================================================================
PARTIAL - some inputs could not be analyzed (results incomplete)

ANALYSIS ISSUES (these inputs were NOT checked)
------------------------------------------------------------------------------
[syntax_error] src/servers/broken.py:6
        SyntaxError: invalid syntax
```

| Status | Meaning | Graded | Exit |
| :--- | :--- | :---: | :---: |
| `clean` | Everything analyzed, nothing fired | ✅ | `0` |
| `findings` | Everything analyzed, issues found | ✅ | `0`/`1` |
| `partial` | Some files could not be read — **not a clean result** | ❌ | `3` |
| `no_matches` | Nothing recognizable was found | ❌ | `0` (`4` with `--fail-on-no-matches`) |
| `error` | Nothing could be analyzed | ❌ | `3` |

Ungraded scans report `score: null` — a grade is a claim about your code, and it is withheld
when part of your code was never read. Parse errors surface in terminal, Markdown, HTML and
SARIF (`toolExecutionNotifications`), never as fake findings. Details:
[`docs/scan-status.md`](docs/scan-status.md).

## Policies and gates

A policy is a named bundle of detection and enforcement settings, so the standard lives in
one reviewable place instead of drifting across laptops and CI.

```bash
vexar-mcp policy                        # what each preset enforces
vexar-mcp scan . --policy strict-prod   # release gate
vexar-mcp scan . --print-config         # exactly what will be enforced, and from which file
```

| Preset | Fails on | Score floor | Token budget / tool | Unignorable |
| :--- | :--- | :---: | :---: | :--- |
| `default` | `error` | — | 250 | — |
| `ci` | `warning` | 80 (B) | 230 | security |
| `strict-prod` | `warning` | 90 (A) | 200 | security |
| `enterprise-governance` | `info` | 97 (A+) | 180 | security, contract |

A protected rule **cannot** be ignored — not from the CLI, not from a config file:

```console
$ vexar-mcp scan . --policy strict-prod --ignore MCP005
vexar-mcp: error: policy 'strict-prod' protects security rules; these cannot be ignored: MCP005.
```

The most common way a security gate dies is not being defeated — it is being quietly
silenced one rule at a time. Exit codes make the distinction explicit: `1` means a gate
failed, `2` means the invocation or configuration was wrong.

Details: [`docs/policies.md`](docs/policies.md).

---

## Adopting a gate on dirty code

```bash
vexar-mcp scan . --write-baseline .vexar-baseline.json     # accept what exists today
vexar-mcp scan . --baseline .vexar-baseline.json           # fail only on what's new
vexar-mcp baseline show .vexar-baseline.json               # inspect accepted risk
```

Fingerprints are `rule + tool + file` — line numbers and message wording are excluded, so
unrelated edits never masquerade as new violations. Baselines are versioned, sorted JSON
designed to be committed and reviewed. Details: [`docs/baseline.md`](docs/baseline.md).

---

## Live server auditing

Static analysis cannot see tools a server generates at runtime. `audit-server` speaks the
protocol:

```bash
vexar-mcp audit-server -- python my_server.py
vexar-mcp audit-server --format sarif --output live.sarif -- npx -y @acme/mcp-server
```

It performs the real `initialize` handshake, follows `tools/list` pagination, validates
protocol conformance (`MCP012`), and runs the entire rule set over the tools that are
actually exposed — through the same normalisation pipeline as file scans, so results are
directly comparable with a static baseline.

The command is argv-only (no shell), bounded by a hard deadline, an 8 MiB response cap, and
a 50-page cap, and the child is killed on expiry. This is the only command that starts a
process; plain `scan` remains pure static analysis. Details:
[`docs/live-audit.md`](docs/live-audit.md).

---

## Rule packs

Organization conventions without forking the tool:

```toml
[[rule]]
id = "ACME001"
slug = "acme-tools-must-be-namespaced"
severity = "warning"
category = "contract"
summary = "All tools must live in the acme_ namespace."
remediation = "Rename the tool with an acme_ prefix."
message = "Tool '{tool}' is outside the ACME namespace."
  [rule.match]
  name_regex = "^acme_"
  invert = true
```

```bash
vexar-mcp scan . --rule-pack ./policies/acme-rules.toml
```

Declarative packs are **data, never executed** — that is the recommended format. Python
packs are supported for teams that audit them like any other dependency. The `MCP` rule-ID
namespace is reserved: a pack cannot shadow a built-in security rule. Details:
[`docs/rule-packs.md`](docs/rule-packs.md).

---

## GitHub Actions

Use the CLI when you want a report in your own pipeline steps. Use the composite action when you want the score, grade, and diagnostic count exposed as step outputs, plus SARIF and a job summary written for you.

```yaml
name: MCP audit
on: [pull_request]

permissions:
  contents: read

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: RANJITHROSAN17/Vexar-MCP@main
        with:
          path: .
          fail-on: error          # error | warning | info | never
          sarif-output: vexar-mcp.sarif
```

| Input | Default | Purpose |
| :--- | :--- | :--- |
| `path` | `.` | File or directory to scan |
| `fail-on` | `error` | Minimum severity that fails the step |
| `max-tokens` | `250` | Per-tool token budget before `MCP002` triggers |
| `ignore` | – | Comma-separated rule IDs to skip |
| `sarif-output` | – | Write a SARIF v2.1.0 file for Code Scanning |

Outputs: `score`, `grade`, `total-diagnostics`. The action needs no token, makes no network calls, and never executes your server.

For inline PR annotations instead of a SARIF upload, run the CLI directly:

```yaml
      - run: pip install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@main"
      - run: vexar-mcp scan . --format github
```

Full workflow recipes, including SARIF upload to the Security tab: [`docs/github-actions.md`](docs/github-actions.md).

---

## Security model

- **No code execution during scans.** Python targets are inspected with `ast.parse` only — no imports, no subprocesses, no plugin loading of scanned files.
- **Two features run code, both opt-in and both documented.** `audit-server` launches a server you name on the command line (argv only, hard deadline, response and pagination caps); Python rule packs are imported only when passed with `--rule-pack`. See [live-audit.md](docs/live-audit.md) and [rule-packs.md](docs/rule-packs.md).
- **No network calls.** Scanning is fully offline; nothing about your source leaves the machine.
- **No secrets in output.** `MCP009` records the *shape* of a credential match (pattern name, variable name, line) and never the literal value, so reports and SARIF uploads cannot become a second copy of a leaked key.
- **Your files are read, not modified.** The tool only writes to stdout or a file you name.
- **Reports cannot become a second leak.** HTML findings are embedded as escaped JSON, so server-controlled strings cannot break out of the document.

**What a finding means:** a static-analysis signal that a risky pattern exists at that line. It is not proof of exploitability, and a clean scan is not proof of safety. Known gaps, false-positive sources, and unsupported inputs are listed in [`docs/limitations.md`](docs/limitations.md).

Reporting a vulnerability in Vexar-MCP itself: see [`SECURITY.md`](SECURITY.md) — please use GitHub Private Vulnerability Reporting, not a public issue.

---

## Dashboard

`vexar-mcp` can emit the JSON a small static dashboard renders. The dashboard is a single self-contained HTML file with no CDN, no build step, and no dependencies:

```bash
vexar-mcp scan examples/ --format json > assets/audit-report.json
python -m http.server 8000    # then open http://localhost:8000/assets/dashboard.html
```

It has explicit states for loading, empty results, missing report, malformed report, and fetch failure — and it will never display invented data as if it were a live scan.

The repository publishes this viewer to GitHub Pages on every push to `main`: `dashboard-deploy.yml` builds an artifact containing `index.html` (the viewer), `dashboard.html` (same file, alias), and `audit-report.json` — a real scan of `examples/` produced during the build. Details: [`docs/dashboard.md`](docs/dashboard.md).

---

## Documentation

| I want to… | Read |
| :--- | :--- |
| Install and scan my server | [`docs/usage.md`](docs/usage.md) |
| Understand a specific finding | [`docs/rules.md`](docs/rules.md) |
| Understand status, coverage and exit codes | [`docs/scan-status.md`](docs/scan-status.md) |
| Enforce a team or production standard | [`docs/policies.md`](docs/policies.md) |
| Adopt the gate on an existing codebase | [`docs/baseline.md`](docs/baseline.md) |
| Audit a running MCP server | [`docs/live-audit.md`](docs/live-audit.md) |
| Add organization-specific rules | [`docs/rule-packs.md`](docs/rule-packs.md) |
| Wire this into CI | [`docs/github-actions.md`](docs/github-actions.md) |
| Know what it will *not* catch | [`docs/limitations.md`](docs/limitations.md) |
| Render a report in the dashboard | [`docs/dashboard.md`](docs/dashboard.md) |
| Understand the internals | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Contribute a rule, loader, or reporter | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| See what's planned | [`.github/roadmap.json`](.github/roadmap.json) |
| Report a security issue | [`SECURITY.md`](SECURITY.md) |

---

## Project status

`0.2.x` — usable, pre-1.0. Rule IDs, exit codes, configuration keys, `vexar-mcp-report/v1`
and `vexar-mcp-baseline/v1` are stable; anything that changes keeps working with a
changelog note. Exit codes `3` (incomplete analysis) and `4` (no matches) were added in
0.2.1; `0`/`1`/`2` are unchanged. See [CHANGELOG.md](CHANGELOG.md) for the versioning
contract.

| Verified signal | Value | How it is measured |
| :--- | :--- | :--- |
| Diagnostic rules | **14** (`MCP001`–`MCP014`) | `vexar-mcp rules` against the current source |
| Enforcement presets | **4** | `vexar-mcp policy` |
| Report formats | **6** (terminal, JSON, SARIF, Markdown, annotations, HTML) | `docs/usage.md`, reporter modules |
| Scan modes | **2** — static files, live stdio JSON-RPC | `scan`, `audit-server` |
| Test suite | **323 tests passing** | `pytest` on Python 3.13 (CI covers 3.10–3.13) |
| Statement coverage | **94%** | `pytest --cov=mcp_doctor` |
| Coverage gate | **85% required** | `--cov-fail-under=85` in `ci.yml` (a threshold, not a result) |
| Quality gates | `ruff`, `ruff format`, `mypy --strict` clean; warnings are errors | CI matrix on Python 3.10 / 3.11 / 3.12 / 3.13 |
| Runtime dependencies | **0** on Python 3.11+ | `pyproject.toml` (`tomli` only on 3.10) |

Milestones and queued work live in [`.github/roadmap.json`](.github/roadmap.json). Coverage and test counts above are a point-in-time local measurement, not a guarantee; the CI badge is authoritative for a given commit.

---

## Contributing

**New here? Start with a [`good first issue`](https://github.com/RANJITHROSAN17/Vexar-MCP/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).**
Each one lists the files involved and the commands to verify your work, so you never have to
guess whether it is done. Need help? Read [`CONTRIBUTING.md`](CONTRIBUTING.md), ask in
[Discussions](https://github.com/RANJITHROSAN17/Vexar-MCP/discussions), or comment on the issue
and a maintainer will answer.

We welcome docs, tests, parser improvements, CLI enhancements, and rule ideas — a false
positive reported with a reproduction snippet is as valuable as a feature.

### Easy wins (under an hour each)

These are small, self-contained, and fully specified. Pick one and open a PR the same day.

| Task | Why it is a good first PR | Issue |
| :--- | :--- | :--- |
| Add rule `MCP015`: flag tool parameters with no description | One rule function, two tests, no parser changes | [#5](https://github.com/RANJITHROSAN17/Vexar-MCP/issues/5) |
| Make the `simulate` price table self-checking | Pure data + one test; no CLI wiring | [RM-030](.github/roadmap.json) |
| Fixed-up corpus of token-estimator accuracy | Test-only; needs care with provenance | [RM-029](.github/roadmap.json) |
| Report constructs the TS loader saw but could not resolve | Touches one loader, well covered by fixtures | [RM-028](.github/roadmap.json) |

If you want a larger piece, `simulate`, rule packs, and the loaders are all small enough to
understand in one sitting — that is a deliberate design goal, not an accident.

1. Browse [`good first issue`](https://github.com/RANJITHROSAN17/Vexar-MCP/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) and [`help wanted`](https://github.com/RANJITHROSAN17/Vexar-MCP/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22).
2. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) — adding a rule is a decorated function plus two tests.
3. Run the same gate CI runs:

```bash
ruff check . && ruff format --check . && mypy src && pytest --cov=mcp_doctor --cov-fail-under=85
```

4. Open a PR titled with [Conventional Commits](https://www.conventionalcommits.org/) (`feat(rules): …`, `fix(ast): …`) and reference the issue.

Project governance, review rules, and bot automation are documented in [`CONTRIBUTOR_RULES.md`](CONTRIBUTOR_RULES.md).

---

<p align="center">
  <a href="LICENSE">MIT licensed</a> · built for the MCP ecosystem · <a href="https://github.com/RANJITHROSAN17/Vexar-MCP/issues/new/choose">report a bug</a>
</p>
