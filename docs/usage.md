# Usage

Everything `vexar-mcp` can do from a shell, verified against the current CLI.

- [Install](#install)
- [Commands](#commands)
- [Scanning targets](#scanning-targets)
- [Report formats](#report-formats)
- [Configuration](#configuration)
- [Suppressing a finding](#suppressing-a-finding)
- [Exit codes](#exit-codes)
- [Python API](#python-api)

---

## Install

**Requirements:** Python 3.10 or newer. Runtime dependencies: none on Python 3.11+ (`tomli` is installed automatically on 3.10 for TOML parsing).

> **Distribution status:** this project does not publish to PyPI yet. `pip install vexar-mcp` will not work, and the `mcp-doctor` name on PyPI belongs to a **different project** by another author. Install from this repository.

```bash
# Recommended: install the current main branch
pip install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@main"

# Isolated CLI install (no changes to your project environment)
pipx install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@main"

# Editable install for development
git clone https://github.com/RANJITHROSAN17/Vexar-MCP.git
cd Vexar-MCP
pip install -e ".[dev]"
```

Verify the install:

```console
$ vexar-mcp --version
vexar-mcp 0.1.0
```

You can also run it without installing the console script:

```bash
python -m vexar_mcp.cli scan .
```

---

## Commands

```text
vexar-mcp [--version] {scan,audit-server,baseline,init,policy,rules,token-report,simulate} ...
```

### `vexar-mcp scan [paths...]`

Scans files or directories and prints a scorecard plus diagnostics.

| Flag | Values | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `paths` | any file or directory | `.` | Targets to scan. Accepts multiple paths. |
| `--format` | `terminal`, `json`, `sarif`, `markdown`, `github` | `terminal` | Output format. |
| `--fail-on` | `error`, `warning`, `info`, `never` | `error` | Minimum severity that produces exit code 1. |
| `--max-tokens` | integer | `250` | Per-tool token budget before `MCP002` triggers. Overrides config files. |
| `--ignore` | comma-separated rule IDs | – | Rules to skip, e.g. `MCP004,MCP008`. |
| `--config` | path | auto-discovered | Explicit TOML configuration file. |
| `--ts-parser` | `lexical`, `tree-sitter` | `lexical` | TypeScript/JavaScript front end. `tree-sitter` needs the optional extra and resolves computed names; see [limitations.md](limitations.md#choosing-a-parser-backend---ts-parser-lexicaltree-sitter). |

```bash
vexar-mcp scan .                                   # everything under the current directory
vexar-mcp scan src/server.py src/tools.json        # specific files
vexar-mcp scan . --format sarif --fail-on warning  # CI gate
vexar-mcp scan . --ignore MCP004 --max-tokens 400  # adjust the policy

pip install "Vexar-MCP[tree-sitter]"
vexar-mcp scan . --ts-parser tree-sitter           # resolve computed tool names (opt-in)
```

### `vexar-mcp token-report [paths...]`

Prints a per-tool leaderboard of estimated context-window cost. Accepts the same `paths` and `--config` arguments as `scan`.

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

### `vexar-mcp simulate [paths...] --model <name>`

Projects the token cost of a catalog into money, for a model and a call volume you choose.
It runs a real scan first and reads token counts from the same estimator that drives `MCP002`
and `MCP014`, so the projection can never contradict a finding.

| Flag | Values | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `--model` | any name in the price table | `claude-sonnet-4.5` | Model to price against. |
| `--calls-per-day` | integer | `1000` | Volume assumption (30-day month). |
| `--input-price` | float | none | Override USD per 1M input tokens (your contract rate). |
| `--format` | `terminal`, `json` | `terminal` | Output format. |
| `--allow-incomplete` | flag | off | Exit 0 even when the scan could not read every file. |

```console
$ vexar-mcp simulate examples --calls-per-day 2000
MODEL: claude-sonnet-4.5  (input $3.00/1M tokens, priced 2026-10)
VOLUME: 2,000 calls/day (~60,000 calls/month)

TOOL NAME                          TOKENS     USD/CALL    USD/MONTH  SOURCE
------------------------------------------------------------------------------------
...
CATALOG TOTAL                         530     0.001590        95.40

Every call pays the catalog tax: 530 tokens x 2,000 calls/day = $95.40/month.
  Removing the costliest tool would save $33.48/month.
```

Three deliberate behaviours:

- **An unknown model is an error (exit 2), not a default price.** A projection built on a
  silently substituted rate is a guess wearing a dollar sign. Use `--input-price` for
  internal or newer models.
- **An incomplete scan is a lower bound (exit 3)**, matching `scan`. Removing that warning
  would make an understated bill look authoritative.
- **No tools found exits 4** rather than printing a confident `$0.00`.

### Price freshness

Rates live in [`src/vexar_mcp/simulate/pricing.json`](../src/vexar_mcp/simulate/pricing.json) as
data, not code, so a repricing is a one-line reviewable diff. Every entry carries its own
`reviewed_on` date.

- An entry older than `REVIEW_WINDOW_DAYS` (180) is **stale**. `simulate` still projects — refusing
  to print a number would not make prices fresher — but the `MODEL:` header gains `REVIEW OVERDUE`
  and a `WARNING:` line names the model, how old it is, and the file to edit.
- The same condition **fails the test suite**, so it cannot be merged unnoticed.
- `--input-price` suppresses the warning: your own contract rate is not in the table, so the
  table's age says nothing about whether that number is right.
- `pricing_as_of`, `pricing_stale` and `pricing_review_window_days` are exposed in `--format json`
  for dashboards that want to gate on it.

### `vexar-mcp init [directory]`

Writes a commented `vexar-mcp.toml` so a project does not start from a blank page, and with
`--github-action` a workflow that gates every push and pull request.

```console
$ vexar-mcp init . --github-action
wrote vexar-mcp.toml
wrote .github/workflows/vexar-mcp.yml

Next:
  1. vexar-mcp scan . --print-config   # confirm the effective settings
  2. vexar-mcp scan .                  # see what it finds today
  3. vexar-mcp baseline write          # optional: gate only on new findings
  4. git add vexar-mcp.toml .github/workflows/vexar-mcp.yml && git commit
```

| Flag | Meaning |
| :--- | :--- |
| `directory` | Project directory to scaffold. Defaults to the current directory; must already exist. |
| `--github-action` | Also write `.github/workflows/vexar-mcp.yml`. |
| `--policy` | Preset whose gate values are written into the config (`default` unless given). |
| `--action-ref` | Ref of `RANJITHROSAN17/Vexar-MCP` the workflow calls. Defaults to `main`. |
| `--force` | Overwrite existing files instead of refusing. |
| `--dry-run` | Print the files without writing anything. |

Four behaviours are deliberate:

- **The generated config is validated before it is written.** It is parsed back through the
  real configuration loader, so `init` cannot hand you a file the next scan rejects.
- **A refusal writes nothing at all.** If any target exists and `--force` was not passed, the
  command exits `2` and leaves the directory untouched — no half-scaffolded projects.
- **An existing configuration is never shadowed silently.** The loader prefers
  `vexar-mcp.toml` over `.mcp-doctor.toml` and pyproject tables, so writing one next to an
  existing config would change how the project is scanned without saying so. `init` names the
  file it found and refuses.
- **`ignore` and `exclude` are generated empty.** A scaffold that pre-silences findings
  produces a clean report, not a clean server.

The workflow tracks `@main` by default, as the roadmap item specifies — which means your CI
changes whenever upstream does. `init` says so on stderr, and `--action-ref` pins it to a
released tag or a commit SHA instead:

```bash
vexar-mcp init . --github-action --action-ref v0.2.4
```

### `vexar-mcp policy [--format terminal|json]`

List the built-in enforcement presets with the fail-on threshold, score floor, token
budget, and protected categories each one applies. See [policies.md](policies.md).

```console
$ vexar-mcp policy
PRESET                   FAIL-ON     MIN  TOK/TOOL  PROTECTED
--------------------------------------------------------------------------------------------
default                  error         0       250  -
ci                       warning      80       230  security
strict-prod              warning      90       200  security
enterprise-governance    info         97       180  security,contract
```

### `vexar-mcp audit-server -- <command>`

Introspect a *running* MCP server over stdio JSON-RPC and scan its announced tool catalog.
See [live-audit.md](live-audit.md).

```bash
vexar-mcp audit-server -- python my_server.py
vexar-mcp audit-server --format json --timeout 60 -- npx -y @acme/mcp-server
```

### `vexar-mcp audit-registry <file>`

Audit **every** MCP server named in a registry file or an existing client configuration
(Claude Desktop, Cursor), and produce one combined report. See
[the registry section of live-audit.md](live-audit.md#auditing-a-whole-fleet).

```bash
vexar-mcp audit-registry .vexar-servers.json
vexar-mcp audit-registry ~/.config/Claude/claude_desktop_config.json --dry-run
vexar-mcp audit-registry fleet.json --format html --output fleet.html
```

Each server is audited in isolation: one that cannot start is reported and the run
continues. A fleet where any server failed or was skipped is **incomplete** and exits `3`,
because findings from a fleet that was only partly audited do not mean what they appear to
mean. `--allow-incomplete` downgrades that to the normal gate result.

The **fleet score is the worst audited server**, not the average. Four clean servers and one
vulnerable one average to a comfortable B, and the B is the only number anyone reads; the
minimum cannot be improved by adding healthy servers. The mean is still reported beside it,
labelled as context.

`--dry-run` validates the file and prints the exact argv that would run, launching nothing.
A registry is a list of commands, so reviewing it before trusting it should not require
running it.

#### Registry file shape

Either a native registry:

```json
{
  "version": 1,
  "servers": [
    {"name": "invoices", "command": ["python", "servers/invoices.py"], "timeout": 45},
    {"name": "search", "command": "npx", "args": ["-y", "@acme/search"], "env": {"API_KEY": "..."}}
  ]
}
```

or a client configuration, read as-is:

```json
{
  "mcpServers": {
    "invoices": {"command": "python", "args": ["servers/invoices.py"], "cwd": "/srv/invoices"}
  }
}
```

Validation fails closed: an unknown shape, both shapes at once, a non-string argv element, a
non-string `env` value, a non-positive `timeout`, or duplicate server names (including names
differing only by case) is exit `2` naming the offending field. An empty registry is an error
rather than a clean fleet. A remote/SSE entry (`url`, or `type` of `http`/`sse`) is
well-formed but cannot be audited by the stdio client, so it is listed as **skipped** and
makes the fleet incomplete — never quietly dropped.

Note that **argv is echoed verbatim** into the report for provenance. Put credentials in
`env`, which is never echoed.

### `vexar-mcp baseline write|show`

Create or inspect accepted-risk snapshots. See [baseline.md](baseline.md).

```bash
vexar-mcp baseline write . --output .vexar-baseline.json
vexar-mcp baseline show .vexar-baseline.json
```

### `vexar-mcp rules [--format terminal|json]`

Lists every registered rule with its ID, severity, category, slug, and summary. Useful for building your own ignore lists or docs.

---

## Gates and adopted risk

`scan` enforces a policy. The most useful flags:

| Flag | Purpose |
| :--- | :--- |
| `--policy <name>` | Apply an enforcement preset (`default`, `ci`, `strict-prod`, `enterprise-governance`) |
| `--fail-on <sev>` | Override the policy threshold (`error`, `warning`, `info`, `never`) |
| `--min-score <0-100>` | Fail when the health score drops below a floor |
| `--ignore <ids>` | Skip rule IDs — rejected if the active policy protects them |
| `--rule-pack <spec>` | Load an opt-in rule pack (repeatable) |
| `--baseline <file>` | Gate only on findings introduced after the baseline |
| `--write-baseline <file>` | Record current findings as accepted risk, then exit |
| `--print-config` | Print the effective configuration, the file it came from, and the full `extends` chain with the layer that supplied each setting, without scanning |
| `--history <file>` | Score history for the HTML trend sparkline; add `--append-history` to record this run |
| `--emit FORMAT=PATH` | Render an extra format from the **same** scan (repeatable, `-` for stdout) |
| `--allow-incomplete` | Do not fail when files cannot be parsed (off by default; exit `3` otherwise) |
| `--fail-on-no-matches` | Fail with exit `4` when no supported MCP tool is found |

```bash
# One analysis, every artefact — never scan the same tree four times
vexar-mcp scan . --emit sarif=vexar.sarif --emit html=report.html --emit json=report.json

# What will CI actually enforce, and from which file?
vexar-mcp scan . --policy strict-prod --print-config

# Adopt on legacy code: accept the backlog, fail only on regressions
vexar-mcp scan . --write-baseline .vexar-baseline.json
vexar-mcp scan . --baseline .vexar-baseline.json --policy strict-prod
```

## Worked CI job: adopt once, gate only new findings

This workflow runs against `examples/` in a fresh checkout of this repository.
Save it as `.github/workflows/vexar-baseline.yml`. It deliberately separates an
**explicit adoption run** from ordinary enforcing runs; do not regenerate a
baseline on every PR, or every regression becomes accepted risk.

```yaml
name: Vexar baseline gate
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      adopt:
        description: Generate a candidate baseline for review (not enforcement)
        type: boolean
        default: false
permissions:
  contents: read
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: '3.12'
      - name: Install
        run: python -m pip install -e ".[dev]"
      - name: Adopt existing findings (manual opt-in only)
        if: github.event_name == 'workflow_dispatch' && inputs.adopt == true
        run: vexar-mcp scan examples/ --policy ci --write-baseline .vexar-baseline-ci.json
      - name: Export candidate for review
        if: github.event_name == 'workflow_dispatch' && inputs.adopt == true
        uses: actions/upload-artifact@v4
        with:
          name: candidate-vexar-baseline
          path: .vexar-baseline-ci.json
          include-hidden-files: true
          if-no-files-found: error
      - name: Enforce the committed baseline
        if: github.event_name != 'workflow_dispatch' || inputs.adopt != true
        run: vexar-mcp scan examples/ --policy ci --baseline .vexar-baseline-ci.json
```

### First run: adoption and review

On the default branch, select **Actions → Vexar baseline gate → Run workflow**
and enable **adopt**. This runs `--write-baseline`, records the existing findings,
and exits successfully despite the examples' intentional violations. Download the
`candidate-vexar-baseline` artifact, review the JSON, then commit
`.vexar-baseline-ci.json` at the repository root in a reviewed PR. The job has
read-only repository permissions and **does not commit accepted risk for you**.
The workflow must be on the default branch for its manual dispatch button to appear.
Until the baseline is committed, enforcing runs fail closed with exit `2`;
that is expected, not a reason to add `continue-on-error`.

For local adoption instead, run the exact command from the adopting step above,
review and commit the generated file, then run the enforcing command. Both scan
commands are executed directly from this YAML by
`tests/test_baseline_ci_docs.py`; the installation command is also used by the
repository's CI. No extra scan commands are hidden behind illustrative output.

### Second run: no changes

With the committed baseline and unchanged `examples/`, the enforcing run exits
`0`: zero new findings, the existing findings are **unchanged**, and zero resolved.
The ordinary score can still be low and the report can still show findings. A
baseline changes what fails the gate, not the measured score or the underlying
code. In baseline mode the score floor is not enforced; only **new findings at or
above the `ci` severity threshold** fail with exit `1`. Incomplete scans retain
their separate failure behavior; a baseline is not a parse-error bypass.

New violations fail; fixed findings become resolved. Changes to rules, tool
schemas or the selected policy can change scores without a source edit. Keep the
policy consistent between adoption and enforcement, and review baseline refreshes
rather than accepting them automatically. Fingerprints omit line numbers and
message text, so moving lines alone does not make a violation new; they also
cannot distinguish another occurrence of the same rule on the same tool/file.
See [baseline.md](baseline.md) for identity details and governance.

For your own project, replace `examples/` in **both** scan steps with your server
folder. The editable install shown here installs Vexar-MCP from this repository;
in a different repository install a reviewed, pinned Vexar-MCP release instead.
Those substitutions are project-specific, not commands verified by this example.

## Scanning targets

| Extension | Parser | What is extracted |
| :--- | :--- | :--- |
| `.py` | `ast.parse` (never imported) | `@mcp.tool()` / `@server.tool()` decorated functions, `Tool(...)` objects, `FastMCP` builders; parameter names, annotations, defaults, bounds; sink calls inside the function body, plus one hop of module-local helpers that receive a tool parameter |
| `.ts`, `.js`, `.jsx` | Static text/AST scan, no Node runtime | TypeScript MCP SDK `server.tool(...)` registrations |
| `.json` | `json.loads` | MCP `tools/list` payloads, OpenAI and Anthropic tool manifests |
| `.yaml`, `.yml` | `yaml.safe_load` (optional PyYAML) | The same manifest shapes as JSON, wrapped in YAML |

Directories are walked recursively. These directory names are skipped by default: `.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, `dist`, `build`. Override with `exclude = [...]` in configuration.

### YAML manifests

YAML support comes from an optional dependency, so the core package keeps its zero-dependency install:

```bash
pip install 'vexar-mcp[yaml]'    # adds PyYAML
```

Three behaviours are worth knowing, because YAML is the one format where most files in a repository are *not* interesting to a scanner:

- **Only manifest-shaped files are analyzed.** A `.yaml`/`.yml` file enters the scan when it contains a `tools:` key or an `inputSchema:`/`input_schema:` definition, matched conservatively so a workflow step writing `tools: cargo` at deep indentation does not qualify. Your CI workflows, `docker-compose.yml`, and `mkdocs.yml` are never parsed and never appear in coverage. Naming a file explicitly (`vexar-mcp scan tools.yaml`) skips the heuristic entirely — an explicit target is an instruction.
- **A missing PyYAML is a failure, not a skip.** If a manifest-shaped file cannot be read because PyYAML is absent, the scan reports a `missing_dependency` parse issue, withholds the grade, and exits `3`. Quietly skipping a manifest full of vulnerable tools and reporting A+ is the exact false confidence the scoring model exists to prevent.
- **Findings carry the real line number.** A tool starting on line 40 of a manifest is reported at line 40, so a finding in a long manifest points somewhere you can look. (JSON manifests fall back to the entry's position in the array, because a decoded document has no line information.)

The composite GitHub Action installs this extra for you, so a workflow that runs
`uses: RANJITHROSAN17/Vexar-MCP@main` analyses YAML manifests without further configuration.

Malformed YAML produces an `invalid_yaml` parse issue with the offending line, and — like any unreadable input — makes the result `PARTIAL` rather than clean.

If a target does not exist, the CLI exits with status `2` and an error message.

---

## Report formats

### Terminal (default)

Human-readable scorecard, one block per finding with a suggested fix.

```console
$ vexar-mcp scan examples/vulnerable_server.py
==============================================================================
Vexar-MCP  |  Health Score: 4/100 (F)  |  Tools: 2 across 1 file(s)
Context Footprint: ~105 tokens  |  Errors: 4  Warnings: 5  Info: 3
==============================================================================

[ERROR] MCP005 (dangerous-shell-execution) at examples/vulnerable_server.py:25 [run]
        Tool 'run' invokes dangerous execution sink `subprocess.run(shell=True)` with parameter 'cmd'.
        Fix: Use `subprocess.run([cmd, arg], shell=False, check=True)` with validated arguments.
```

The health score starts at 100 and subtracts `15` per error, `6` per warning, and `2` per info finding, floored at 0. Grades: `A+` ≥ 97, `A` ≥ 90, `B` ≥ 80, `C` ≥ 70, `D` ≥ 55, otherwise `F`.

### JSON

The complete report: summary, every tool with its token estimate, and every diagnostic with remediation text. This is the stable machine-readable contract and the input the [dashboard](dashboard.md) renders.

```bash
vexar-mcp scan . --format json > audit-report.json
```

### SARIF v2.1.0

For GitHub Code Scanning and any SARIF-consuming IDE. Includes the full rule catalog so the Security tab shows rule metadata.

```bash
vexar-mcp scan . --format sarif > vexar-mcp.sarif
```

### Markdown

A compact table for `$GITHUB_STEP_SUMMARY` or a sticky PR comment.

```console
$ vexar-mcp scan examples/vulnerable_server.py --format markdown
## Vexar-MCP Audit Summary
| Health Score | Grade | Risk | Tools Audited | Est. Context Tax | Errors | Warnings | Info |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **4/100** | **F** | 98 (critical) | 2 | ~105 tokens | 4 | 5 | 3 |
```

### HTML

A single self-contained file — no CDN, no fonts, no external scripts, works offline and
renders in restricted viewers:

```bash
vexar-mcp scan . --format html --output vexar-report.html
vexar-mcp scan . --format html --output vexar-report.html --history scores.json --append-history
```

Includes score and risk scorecards, an inline SVG trend sparkline when `--history` is used,
severity filters, free-text search, a baseline delta block, and a tool catalogue ranked by
context cost.

### GitHub Actions annotations

Workflow commands that GitHub renders as inline annotations on PR diff lines — no SARIF upload and no Advanced Security needed.

```console
$ vexar-mcp scan examples/vulnerable_server.py --format github
::error file=examples/vulnerable_server.py,line=25,title=MCP005 (dangerous-shell-execution)::[run] Tool 'run' invokes dangerous execution sink `subprocess.run(shell=True)` with parameter 'cmd'. Fix: ...
::warning file=examples/vulnerable_server.py,line=33,title=MCP006 (unvalidated-ssrf-sink)::[fetch_and_save_remote_blob] ...
::notice file=examples/vulnerable_server.py,line=23,title=MCP004 (unbounded-collection-or-limit)::[run] ...
```

Source paths are emitted repo-relative so annotations attach to the right diff line. A clean scan prints nothing.

---

## Configuration

Vexar-MCP reads TOML, discovered from the directory of the first scan target upward:

1. `<dir>/vexar-mcp.toml`
2. `<dir>/.mcp-doctor.toml` — legacy filename, still supported
3. `<dir>/pyproject.toml` under `[tool.vexar-mcp]`, falling back to `[tool.mcp-doctor]`

```toml
# vexar-mcp.toml
max-tool-tokens = 250        # per-tool budget before MCP002 fires
max-server-tokens = 4000     # server-wide budget
ignore = ["MCP004"]          # rule IDs to skip entirely
exclude = [".git", "node_modules", "vendor"]
```

Equivalent in `pyproject.toml`:

```toml
[tool.vexar-mcp]
max-tool-tokens = 250
ignore = ["MCP004"]
```

Precedence: `--max-tokens` beats the config file, which beats the built-in default. `--ignore` is merged with, not substituted for, `ignore` in the file. An explicit `--config path.toml` bypasses discovery entirely.

---

## Suppressing a finding

Add a `noqa` comment naming the rule IDs to the line the diagnostic points at (usually the decorator or the sink line):

```python
@mcp.tool()  # noqa: MCP001
def run_shell_task(cmd: str) -> str: ...
```

Multiple rules: `# noqa: MCP001,MCP008`. Use `# noqa: ALL` to silence everything for that tool.

Prefer `ignore` in configuration for project-wide policy and `noqa` for genuinely intentional, individually reviewed exceptions — a bare `noqa` with no rule list is not supported.

---

## Exit codes

| Code | Meaning |
| :--- | :--- |
| `0` | Scan completed and no finding met the `--fail-on` threshold |
| `1` | Scan completed and at least one finding met the threshold |
| `2` | Usage error, missing target, or unreadable file |
| `3` | Analysis incomplete — some inputs could not be analyzed (`scan`), or a server in a fleet could not be audited (`audit-registry`) |
| `4` | No supported MCP tool was found and the policy requires at least one |

`--fail-on never` always returns `0` for a successful scan, which is what you want when you only need a report artifact.

---

## Python API

```python
from pathlib import Path

from vexar_mcp import ScanConfig, scan_paths
from vexar_mcp.reporters import render_sarif

report = scan_paths([Path("src")], ScanConfig(max_tool_tokens=300))
print(report.summary.score, report.summary.grade)
for diagnostic in report.diagnostics:
    print(diagnostic.rule_id, diagnostic.source_file, diagnostic.line)

Path("out.sarif").write_text(render_sarif(report), encoding="utf-8")
```

`scan_paths` raises `FileNotFoundError` for a missing target. Nothing in the scanned file is executed.

## pre-commit hooks

Two hooks are provided in `.pre-commit-hooks.yaml`:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/RANJITHROSAN17/Vexar-MCP
    rev: v0.2.0
    hooks:
      - id: vexar-mcp            # default profile, changed files only
      - id: vexar-mcp-policy     # strict-prod gate on changed files
```

The `vexar-mcp-live` hook is defined but not enabled by default, because it needs the exact
server command for your project:

```yaml
      - id: vexar-mcp-live
        args: ["--", "python", "my_server.py"]
```

## Token-estimator accuracy benchmark

The scanner's token counts remain **heuristic estimates**, not billed usage or an
accuracy guarantee. The old “within ~8%” claim was unsupported and has been removed.
The formula is unchanged by this benchmark work; do not interpret disclosure of
measurement error as a calibration fix.

The committed [reference report](../benchmarks/token_report.json) contains all raw
counts and mean, p95 and worst-case **absolute percentage error** for each tokenizer
encoding and serialization separately. See the [corpus and methodology](../benchmarks/README.md).
The small synthetic corpus exposes substantial errors; it is not a random sample
of production tools and cannot justify a general accuracy claim.

Reproduce from the repository root (installation and first use may need network):

```bash
python -m pip install -e ".[exact-tokenizer]"
python scripts/benchmark_tokens.py --check
```

To regenerate after deliberately changing the estimator or fixtures:

```bash
python scripts/benchmark_tokens.py
```

Review the report diff; CI recomputes it with the pinned tokenizer. Do not hand-edit
measurements. The optional `exact-tokenizer` extra installs `tiktoken==0.13.0` for
this benchmark; it **does not switch the CLI to exact counting**. Default scans do
not import/download it. On first use tiktoken may download its hash-verified encoding
tables; later runs can use its cache (`TIKTOKEN_CACHE_DIR` can select the cache).
The dedicated benchmark CI job opts in; normal runtime dependencies remain unchanged.

Both `cl100k_base` and `o200k_base` count identical compact JSON for each fixture,
with Unicode escaping enabled and disabled as separate groups. These are encoding
references, not model-specific chat protocol accounting. The `+8` heuristic in the
raw-schema branch of `estimate_tool_tokens` is disclosed separately and excluded
from the error calculation. Its parameter-based fallback is not measured here.
