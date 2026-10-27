# GitHub Actions

Two ways to run Vexar-MCP in CI. Pick based on what you need.

| | Composite action | CLI in your own step |
| :--- | :--- | :--- |
| Setup | One `uses:` line | `pip install` step + `run:` steps |
| Step outputs (`score`, `grade`, `total-diagnostics`) | ✅ | ✗ (parse JSON yourself) |
| SARIF file written for you | ✅ | ✗ (redirect stdout) |
| Job summary written for you | ✅ | ✗ |
| Inline PR annotations | ✗ | ✅ `--format github` |
| Full control over pipeline steps | ✗ | ✅ |

Both are equally offline: neither needs a token, a network call at scan time, or an upload.

---

## Option 1 — composite action

The action installs Vexar-MCP, runs a SARIF scan, writes a Markdown summary to the job, exposes outputs, and finally runs the terminal scan with your `fail-on` policy (that last step is the one that can fail the job).

```yaml
name: MCP audit

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Vexar-MCP scan
        id: audit
        uses: RANJITHROSAN17/Vexar-MCP@main
        with:
          path: .
          fail-on: error
          max-tokens: "250"
          sarif-output: vexar-mcp.sarif

      - name: Print the score
        run: |
          echo "score=${{ steps.audit.outputs.score }} grade=${{ steps.audit.outputs.grade }}"
          echo "diagnostics=${{ steps.audit.outputs.total-diagnostics }}"
```

### Inputs

| Input | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `path` | no | `.` | File or directory to scan |
| `fail-on` | no | `error` | `error`, `warning`, `info`, or `never` |
| `max-tokens` | no | `250` | Per-tool token budget |
| `ignore` | no | – | Comma-separated rule IDs, e.g. `MCP004,MCP008` |
| `sarif-output` | no | – | Path to write a SARIF v2.1.0 report |

### Outputs

| Output | Description |
| :--- | :--- |
| `score` | Health score, `0`–`100` |
| `grade` | Letter grade, `A+` … `F` |
| `total-diagnostics` | Number of findings in the scan |

### Uploading SARIF to the Security tab

Code Scanning requires a **private** repository or GitHub Advanced Security on a private one; public repositories have Code Scanning available at no cost. The scan itself never needs it — this step is optional.

```yaml
      - name: Upload SARIF
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: vexar-mcp.sarif

permissions:
  contents: read
  security-events: write   # required by upload-sarif
```

---

## Option 2 — CLI steps

### Inline PR annotations

Renders findings directly on the changed lines of the PR diff. No SARIF upload, no Advanced Security, works on forks.

```yaml
name: MCP annotations

on: pull_request

permissions:
  contents: read

jobs:
  annotate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Vexar-MCP
        run: pip install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@main"

      - name: Scan with inline annotations
        run: vexar-mcp scan . --format github --fail-on error
```

### JSON artifact

```yaml
      - name: Scan
        run: vexar-mcp scan . --format json --fail-on never > vexar-report.json

      - uses: actions/upload-artifact@v4
        with:
          name: vexar-mcp-report
          path: vexar-report.json
```

### Job summary

```yaml
      - name: Append report to job summary
        if: always()
        run: |
          vexar-mcp scan . --format markdown --fail-on never >> "$GITHUB_STEP_SUMMARY"
```

---

## Pinning a version

`@main` tracks the latest commit. Pin a release tag for reproducible CI:

```yaml
uses: RANJITHROSAN17/Vexar-MCP@v0.1.0
```

For the CLI, pin the same ref in the install URL:

```bash
pip install "git+https://github.com/RANJITHROSAN17/Vexar-MCP.git@v0.1.0"
```

## What this will not do

- It does not comment on the PR or edit code. Findings are annotations and reports only.
- It does not block merges by itself. `fail-on` fails the *step*; branch protection decides whether a failed check blocks the merge.
- It does not execute the scanned server, so it cannot detect runtime-only misconfigurations. See [Limitations](limitations.md).
