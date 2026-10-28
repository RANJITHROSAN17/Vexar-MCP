# Quickstart

Two minutes, four steps. Every command below was run against this repository to produce the
output shown, so if yours differs, that is a bug — please [open an issue](https://github.com/RANJITHROSAN17/Vexar-MCP/issues/new/choose).

- [Step 0 — what you need](#step-0--what-you-need)
- [Step 1 — install](#step-1--install)
- [Step 2 — see it work](#step-2--see-it-work)
- [Step 3 — run it on your own server](#step-3--run-it-on-your-own-server)
- [Step 4 — make it a gate](#step-4--make-it-a-gate)
- [What next](#what-next)
- [When something looks wrong](#when-something-looks-wrong)

---

## Step 0 — what you need

| | |
| :--- | :--- |
| **Python** | 3.10 or newer. Check with `python3 --version`. |
| **A server to scan** | Optional for steps 1–2, which use the bundled broken example. |
| **Runtime dependencies** | None on 3.11+ (`tomli` only on 3.10). No network calls, no telemetry. |

You do **not** need an MCP client, an API key, or a running server. Static scanning is pure
local analysis.

---

## Step 1 — install

There is no PyPI release yet, so install from source:

```bash
pip install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@main"
vexar-mcp --version
```

```console
vexar-mcp 0.2.1
```

If `vexar-mcp` is not found, the install went into a different environment than your shell is
using. `python3 -m venv .venv && source .venv/bin/activate` first, then reinstall.

---

## Step 2 — see it work

Scan the intentionally insecure example that ships with the project:

```bash
vexar-mcp scan examples/vulnerable_server.py
```

You should see a low score, a handful of findings with exact line numbers and a concrete fix
for each, and an **exit code of `1`**:

```console
Vexar-MCP  |  Health Score: 8/100 (F)  |  Tools: 2 across 1 file(s)
...
[ERROR] MCP005 (dangerous-shell-execution) at examples/vulnerable_server.py:25 [run]
        Tool 'run' invokes dangerous execution sink `subprocess.run(shell=True)` with parameter 'cmd'.
        Fix: Use `subprocess.run([cmd, arg], shell=False, check=True)` with validated arguments.
```

Open `examples/vulnerable_server.py` next to that output and the rule set stops being abstract:
each finding points at the line that causes it.

Now scan the reference implementation, which is written to pass every rule:

```bash
vexar-mcp scan examples/clean_server.py
```

```console
Vexar-MCP  |  Health Score: 100/100 (A+)  |  Tools: 2 across 1 file(s)
All checks passed! Zero security, schema, contract, or token-budget findings.
```

**That contrast is the product.** `8/100` on one file and `100/100` on the other, from the same
command, with the difference explained line by line.

---

## Step 3 — run it on your own server

```bash
vexar-mcp scan path/to/your_server        # a file, a directory, or a JSON manifest
```

Reading the output, in order of importance:

1. **`Status:`** — `CLEAN`, `FINDINGS`, `PARTIAL`, `ERROR`, or `NO_MATCHES`. If this is not
   `CLEAN` or `FINDINGS`, stop and read why: the scan did not fully understand your code.
2. **`Coverage:`** — how many files were found, analyzed, and failed. A big gap between
   candidate and analyzed files is the single most important number in the report.
3. **The findings** — each has a stable ID, a file:line, a severity, a risk score and a fix.

Three commands worth knowing immediately:

```bash
vexar-mcp token-report .        # what your tool catalog costs on every single request
vexar-mcp rules                 # all 14 rules, with severity and what each one flags
vexar-mcp scan . --format html --output report.html   # one self-contained file to share
```

<details>
<summary><strong>Nothing was found, and you expected findings</strong></summary>

That is usually accurate, not a bug: the loaders only recognise tool registration patterns they
can see statically. Generated tools, decorators defined in another module, or a TypeScript
factory function will not appear. Check `Status:` first — if it says `NO_MATCHES`, the scanner
found no tool at all, and [docs/limitations.md](docs/limitations.md) lists the shapes that are
invisible on purpose.

For those servers, audit the running process instead:

```bash
vexar-mcp audit-server -- python my_server.py
```
</details>

---

## Step 4 — make it a gate

A report nobody gates on is a report nobody reads. Adopt in two moves so existing problems do
not block you on day one:

```bash
# 1. Accept what already exists
vexar-mcp scan . --write-baseline .vexar-baseline.json

# 2. Fail only on new findings, under a named policy
vexar-mcp scan . --baseline .vexar-baseline.json --policy strict-prod
```

Then wire it into CI:

```yaml
- uses: RANJITHROSAN17/Vexar-MCP@main
  with:
    paths: .
    policy: strict-prod
    allow-incomplete: false
```

`allow-incomplete: false` is the default and the point: if the job could not read your code, the
job fails rather than reporting a comfortable zero. See [policies](docs/policies.md) and
[GitHub Actions](docs/github-actions.md).

---

## What next

| If you want to… | Read |
| :--- | :--- |
| Understand every rule and tune the budgets | [docs/rules.md](docs/rules.md) |
| Pick a preset, or write your own | [docs/policies.md](docs/policies.md) |
| Suppress one finding, justified, in a review | [Configuration](docs/usage.md#configuration) |
| Know exactly when a scan is trustworthy | [docs/scan-status.md](docs/scan-status.md) |
| Audit a running server over stdio | [docs/live-audit.md](docs/live-audit.md) |
| Add your own rules without forking | [docs/rule-packs.md](docs/rule-packs.md) |
| Contribute a rule, loader or reporter | [CONTRIBUTING.md](CONTRIBUTING.md) |

---

## When something looks wrong

| Symptom | Meaning |
| :--- | :--- |
| `Health Score: n/a`, `Status: ERROR`, exit `3` | Nothing could be analyzed. This is not a pass. Read the `ANALYSIS ISSUES` block. |
| `Status: PARTIAL`, exit `3` | Some files were analyzed, some were not. The result is incomplete, not clean. |
| `NO_MATCHES`, exit `0` | No matches: no tool was found at all, which usually means a wrong path or an unsupported pattern. Add `--fail-on-no-matches` to turn this into exit `4`, so a misconfigured scan cannot pass silently in CI. |
| `vexar-mcp: error: …`, exit `2` | Configuration, usage, or I/O problem — a rule name, a policy, or a path. |
| Findings you believe are wrong | A false positive. Please [report it](https://github.com/RANJITHROSAN17/Vexar-MCP/issues/new/choose) with the snippet; they are treated as bugs, not opinions. |
