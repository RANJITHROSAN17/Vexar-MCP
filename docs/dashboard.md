# Dashboard

`assets/dashboard.html` is a single-file report viewer for Vexar-MCP JSON output. It is a static page: no build step, no framework, no CDN, no third-party scripts, and no network calls other than reading the report file next to it.

## Generate a report

The dashboard reads `./audit-report.json`, which is exactly the stdout of a JSON scan:

```bash
# from the repository root
vexar-mcp scan examples/ --format json > assets/audit-report.json
```

Then open or serve the folder:

```bash
# simplest: serve the directory over HTTP (recommended)
python -m http.server 8000
# then visit http://localhost:8000/assets/dashboard.html

# or open the file directly
open assets/dashboard.html      # macOS
xdg-open assets/dashboard.html  # Linux
```

`audit-report.json` is not committed — it is generated output. `.gitignore` covers it so a report from your machine never lands in a commit.

> **Why HTTP matters:** browsers block `fetch()` for `file://` pages. Opened directly from disk, the dashboard cannot read the report and falls back to the bundled sample. Serving the folder over HTTP, as above, loads real data.

## What it shows

| Section | Source field |
| :--- | :--- |
| Health score, grade, meter | `summary.score`, `summary.grade` |
| Error / warning / info counts | `summary.errors`, `summary.warnings`, `summary.infos` |
| Tools and files scanned | `summary.tools_scanned`, `summary.files_scanned` |
| Estimated context tokens | `summary.total_estimated_tokens` |
| Findings, filterable by severity | `diagnostics[]`, with `rule_id`, `rule_slug`, `severity`, `message`, `remediation`, `source_file`, `line`, `tool_name` |
| Per-tool token table | `tools[]`, with `name`, `estimated_tokens`, `parameter_count`, `source_file`, `line` |
| Report metadata | `version`, load source, data state, timestamp |

Sections with no data are hidden rather than rendered empty.

## States

The viewer distinguishes every case instead of showing a blank page or invented numbers:

| State | Behaviour |
| :--- | :--- |
| Loading | Skeleton rows, `Loading report…` badge |
| Loaded | Live data, `Report loaded` badge, metadata names the source file |
| Empty | Zero diagnostics: an explicit "No findings" panel, perfect score shown as reported |
| Missing | `404`: bundled sample shown with a banner explaining it is **not** a live scan |
| Invalid | File exists but fails schema validation: sample shown with the validation reason |
| Request failed | Non-2xx or `file://` fetch block: sample shown with the reason |

In every fallback the badge reads **Sample preview** (or **Invalid report**) and the metadata block states *"Sample data — not a live scan"*. The bundled sample is real `vexar-mcp` output for `examples/vulnerable_server.py`, so it stays honest about what the tool produces without pretending to be your scan.

## Safety properties

- **No injection.** All report values are written with `textContent` / `createElement`, never `innerHTML`. A crafted finding message cannot execute script.
- **Validation before render.** A report must have a `summary` object with numeric `score` and `tools_scanned`, plus well-formed `diagnostics` and `tools` arrays, or it is rejected as invalid.
- **No secrets.** Reports contain findings, not source contents; `MCP009` never records credential values in the first place.
- **No third-party requests.** The page loads nothing from a CDN, so it works offline and cannot leak what you scanned.

## Hosting

`dashboard-deploy.yml` publishes the viewer to GitHub Pages on every push to `main` that touches the viewer, the examples, or the scanner. Once Pages is enabled in repository settings, the site is served at:

```text
https://ranjithrosan17.github.io/Vexar-MCP/              -> the viewer (index.html)
https://ranjithrosan17.github.io/Vexar-MCP/dashboard.html -> same file, legacy alias
https://ranjithrosan17.github.io/Vexar-MCP/audit-report.json -> scan of examples/, built at deploy time
```

Three details matter if you fork this:

1. **`index.html` is required.** GitHub Pages serves `index.html` for a directory request. Publishing only `dashboard.html` leaves the site root returning a 404 with the file still reachable by its full name — which is exactly what happened before this was fixed.
2. **The report is generated, not committed.** The workflow runs `vexar-mcp scan examples/ --format json --fail-on never` and writes the result into the artifact, so the public page shows real tool output rather than the bundled sample. It scans the repository's intentionally flawed fixtures, which is why the demo page reports a low score — it is a demo of what findings look like, not a health claim about the project.
3. **The artifact is a directory.** `upload-pages-artifact` must point at a folder (`_site`); pointing it at a single file publishes a site with one unrooted document.

Everything else stays local: `audit-report.json` is git-ignored, so a report produced on your machine never becomes a commit, and a fork's Pages site only ever shows data generated inside its own workflow.

If you publish a report yourself, remember it contains file paths and line numbers from the scanned codebase.
