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
python -m mcp_doctor.cli scan .
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

```bash
vexar-mcp scan .                                   # everything under the current directory
vexar-mcp scan src/server.py src/tools.json        # specific files
vexar-mcp scan . --format sarif --fail-on warning  # CI gate
vexar-mcp scan . --ignore MCP004 --max-tokens 400  # adjust the policy
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
| `--print-config` | Print the effective configuration and the file it came from, without scanning |
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

## Scanning targets

| Extension | Parser | What is extracted |
| :--- | :--- | :--- |
| `.py` | `ast.parse` (never imported) | `@mcp.tool()` / `@server.tool()` decorated functions, `Tool(...)` objects, `FastMCP` builders; parameter names, annotations, defaults, bounds; sink calls inside the function body |
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

`--fail-on never` always returns `0` for a successful scan, which is what you want when you only need a report artifact.

---

## Python API

```python
from pathlib import Path

from mcp_doctor import ScanConfig, scan_paths
from mcp_doctor.reporters import render_sarif

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
