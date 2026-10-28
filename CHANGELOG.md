# Changelog

All notable changes to Vexar-MCP are documented here. This project follows
[Semantic Versioning](https://semver.org/) and
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Versioning contract, so upgrades are predictable:

- **Patch** — fixes, new rules, new presets. Existing rules, exit codes, configuration
  keys, and report fields keep working.
- **Minor** — new commands, new configuration keys, new output fields. Additive only;
  existing invocations keep their behaviour unless a changelog "Changed" note says
  otherwise.
- **Major** — removed flags, renamed rules, changed exit-code semantics, or a breaking
  change to `vexar-mcp-report/v1` / `vexar-mcp-baseline/v1`.

---

## [0.2.0] - 2026-10-28

The release that turns a scanner into a policy-driven analysis tool: enforcement presets,
baseline gating, live server auditing, deterministic risk scoring, an HTML report, and an
opt-in rule pack system.

### Added

- **Policy engine** (`mcp_doctor.policies`): four versioned presets — `default`, `ci`,
  `strict-prod`, `enterprise-governance` — bundling fail-on threshold, score floor, and
  token budgets. Ignoring a rule in a category the preset protects is a hard configuration
  error (exit `2`), so a security profile cannot be silently disabled. See
  [docs/policies.md](docs/policies.md).
- **Baseline gating** (`mcp_doctor.baseline`): `--baseline`, `--write-baseline`, and
  `vexar-mcp baseline write|show`. Fingerprints exclude line numbers and message text, so
  unrelated edits never look like new findings. Malformed baselines fail closed. See
  [docs/baseline.md](docs/baseline.md).
- **Live server auditing** (`mcp_doctor.live`): `vexar-mcp audit-server -- <command>` runs
  the real `initialize` handshake over stdio JSON-RPC 2.0, follows `tools/list` pagination,
  and feeds the result through the same normalisation and rule pipeline as file scans.
  Bounded by a deadline, an 8 MiB response cap, and a 50-page cap. See
  [docs/live-audit.md](docs/live-audit.md).
- **Deterministic risk scoring** (`mcp_doctor.risk`): per-finding 0–100 risk
  (severity × exploitability) and a saturating 0–100 `risk_index`, so a tainted SSRF sink
  outranks a missing description instead of tying with it.
- **HTML report** (`--format html`): one self-contained file with scorecards, severity
  filters, text search, tool catalogue, optional baseline delta, and an inline SVG trend
  sparkline from `--history`. No CDN, fonts, or external scripts — archivable as a
  compliance artefact and safe to open offline.
- **Rule packs** (`mcp_doctor.rules.plugin`): declarative `.toml`/`.json` packs (data only,
  never executed) plus explicitly trusted Python packs. Reserved `MCP` prefix, duplicate
  IDs, duplicate slugs, unknown match keys, and invalid patterns are all rejected. `invert`
  expresses requirements ("every tool must be namespaced"), not just bans. See
  [docs/rule-packs.md](docs/rule-packs.md).
- **Rule `MCP012`** — live protocol and transport validation (unsupported revision, missing
  `tools` capability, malformed `tools/list` entries, runaway pagination).
- **`--min-score`** gate and `--print-config` (effective configuration plus the file it came
  from) on `scan`; `vexar-mcp policy` to list presets.
- `--history` / `--append-history` for score trends without any server.
- SARIF: `partialFingerprints`, `security-severity` per rule (so GitHub Code Scanning ranks
  alerts), and policy metadata on the run.
- JSON report: `schema` (`vexar-mcp-report/v1`), `policy`, `risk_index`, `risk_band`,
  per-diagnostic `risk` and `fingerprint`, and an optional `baseline` delta block.
- Configuration discovery now walks up from the scan target to the first `.git`, so one
  project-level `vexar-mcp.toml` applies to subdirectory scans. Stops at the VCS root so a
  parent checkout cannot leak configuration.
- `mcp_doctor.rules.snapshot_registry()` / `restore_registry()` for scoped pack loading.
- `vexar-mcp.toml` at the repository root, so the project scans itself with documented
  settings.
- `CHANGELOG.md` and this versioning contract.
- 111 new tests (264 total, 94% statement coverage).

### Changed

- Configuration keys are validated strictly: an unknown key, an invalid severity, or a
  non-integer budget is now an error instead of being ignored.
- Configuration and policy errors exit `2` (usage), distinctly from `1` (gate failure).
- `ScanConfig` gained `policy_name`, `fail_on`, `min_score`, and `rule_packs`;
  `ScanSummary` gained `risk_index`; `ScanReport` gained `policy_name`; `Diagnostic` gained
  `risk`. All are additive with defaults, so existing library callers are unaffected.
- Terminal, Markdown, and JSON reports now include policy name and risk.
- `pytest` treats warnings as errors, which caught two real resource bugs in the live audit
  path during development (unclosed subprocess pipes and a pipe-close ordering deadlock).

### Fixed

- Repaired three governance workflows that were failing on every push at startup (GitHub
  reported a failed run with no jobs, which reads as a bot malfunction):
  `code-review-bot.yml` had an unquoted `: ` inside two step names, which is invalid YAML,
  and `code-review-bot.yml`, `ai-code-review-bot.yml` and `final-merge-approval-bot.yml`
  used the permission scope `pull_requests` instead of the real scope `pull-requests`.
- Added `.github/scripts/validate_workflows.py`, wired into CI, so an invalid workflow or
  action manifest fails a normal check with a file-and-line message instead of poisoning
  the Actions tab.
- Removed the orphaned `rules/mcp009_protocol.py`, which was never imported but would have
  overwritten `MCP009` (hardcoded secret detection) with a silent no-op had anyone wired it
  up. Its `protocol-legacy-transport` intent is now covered by `MCP012` against live
  servers, where transport can actually be observed.
- The live audit now terminates the child process *before* closing pipes; closing first
  could deadlock the audit for the full server timeout. Every pipe is closed after the
  child is reaped.

### Security

- HTML report embeds finding data as escaped JSON (`<`, `>`, `&` → JSON unicode escapes), so
  server-controlled strings such as tool names cannot break out of the script element.
- Documented the trust boundary for the two features that involve running code: live server
  auditing and Python rule packs. Static `scan` still never imports or executes scanned
  files.

---

## [0.1.0]

Initial pre-release: static analysis for Python, TypeScript, and JSON tool manifests with
rules `MCP001`–`MCP011` and `MCP013`, terminal/JSON/SARIF/Markdown/GitHub reporters, a
composite GitHub Action, and a static dashboard.
