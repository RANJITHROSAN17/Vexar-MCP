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

## [Unreleased]

(Reserved for the next release.)

---

## [0.2.6] - 2026-10-28

Security rules now see through module-local helper functions, closing
[issue #44](https://github.com/RANJITHROSAN17/Vexar-MCP/issues/44). A tool that shells out
through a private `_run_cmd(cmd)` helper used to scan as clean, because the sink was in
another function in the same file. This is the shape most production MCP servers have.

### Added

- **One-hop taint propagation** for `MCP005`–`MCP007`, `MCP011` and `MCP013`. When a tool
  passes one of its parameters into a module-level helper, sinks inside that helper are
  attributed to the tool — reported against *the tool's* parameter name, at the helper's
  line, so the finding points at the code that causes it.

  ```python
  def _run_cmd(cmd):
      return subprocess.run(cmd, shell=True)  # <- the sink


  @mcp.tool()
  def deploy(command: str) -> str:
      return _run_cmd(command)  # <- MCP005, for 'command'
  ```

  Four behaviours keep this from becoming noise:

  - **Taint is required.** A helper containing `os.system("ls")` that the tool calls with no
    arguments produces nothing, because the tool cannot influence that command.
  - **Guards are inherited, both ways.** Validating a URL in the tool body before delegating
    suppresses `MCP006` for the helper's request; a guard inside the helper covers its own
    sinks.
  - **Mapping is positional and by keyword, never guessed.** An unmappable `*args`/`**kwargs`
    spread is skipped rather than attributed to the wrong parameter.
  - **One hop, and recursion-safe.** `tool -> _outer -> _inner` is out of scope; a visited set
    bounds mutual recursion independently of the hop limit, so raising it later cannot loop.

  Methods (`self._run`), closures and imported functions end the trace: following them needs
  type or inter-module analysis, and a guessed trace produces findings nobody can explain.

- `examples/helper_delegation_server.py`, scanned by CI, showing four delegated tools: two
  with findings, one whose helper confines its own path, and one whose helper runs a command
  the tool cannot influence. A test holds each of those outcomes.

### Changed

- The sink classifier is now one function (`_classify_call`) shared by direct analysis and
  helper tracing, so a helper-derived finding and the same code written inline cannot drift
  apart. A test asserts the two produce identical rule sets.

### Notes

- No change to existing findings: `examples/` and the clean reference server score exactly as
  before this release, and the whole existing suite passes untouched.


---

## [0.2.5] - 2026-10-28

Adds YAML tool manifests, closing [issue #40](https://github.com/RANJITHROSAN17/Vexar-MCP/issues/40).
Agent frameworks and declarative MCP gateways routinely keep tool schemas in YAML, which was
a blind spot: a repository whose tools lived in `tools.yaml` scanned as having no tools at all.

### Added

- **YAML manifest support** for `*.yaml` and `*.yml`, accepting the same four document shapes
  as the JSON loader (`tools:` list, `result.tools` envelope, a single tool mapping, a bare
  list) with the same rules, scoring, and `ToolSpec` normalization.
- **`vexar-mcp[yaml]` extra.** PyYAML stays optional, so the core install keeps zero runtime
  dependencies.

Three behaviours are deliberate:

- **Discovery only treats manifest-shaped YAML as a candidate.** A file is parsed when it
  contains a `tools:` or `inputSchema:`/`input_schema:` key at shallow indentation. Without
  that gate, every `.github/workflows/*.yml` in every repository would enter the scan, and
  counting them as "analyzed files with no tools" would misreport coverage. Non-manifest YAML
  is never parsed and never becomes a parse issue. An explicitly named file skips the check.
- **A missing PyYAML fails loudly instead of skipping.** A manifest that cannot be read
  produces a `missing_dependency` parse issue, withholds the grade, and exits `3`, naming the
  install command. Silently skipping a file full of vulnerable tools and reporting A+ is the
  failure mode the scoring model exists to prevent.
- **Findings carry the tool's real line.** A YAML manifest is parsed with a `SafeLoader`
  subclass that records each mapping's source line, so a finding on the fortieth tool points
  at line 40 instead of line 1.

`yaml.load`, `full_load` and `unsafe_load` are never used: only `SafeLoader` touches scanned
input, and a test pins the loader class so a later refactor cannot quietly swap it.

### Changed

- Parameter line numbers in JSON and YAML manifests now follow their tool's line instead of a
  fixed `1`, so all findings about one tool agree on a location (previously `MCP015` pointed at
  line 1 while `MCP008` pointed at the right line).
- `parse_tool_entries` accepts optional per-entry lines; JSON callers are unchanged.

### Fixed

- **`rulesets/contributor-enforcement.json` was invalid JSON** — a stray backslash after
  `"bypass_actor_ids": [100000000]`. The ruleset could not have been imported into GitHub, and
  it made `vexar-mcp scan .` on this repository report an unanalyzed input.


---

## [0.2.4] - 2026-10-28

Adds `vexar-mcp init`, the adoption path that was missing: the tool could tell you what was
wrong but not help you start. Closes [issue #7](https://github.com/RANJITHROSAN17/Vexar-MCP/issues/7).

### Added

- **`vexar-mcp init [directory]`** writes a commented `vexar-mcp.toml`, and with
  `--github-action` a workflow that gates every push and pull request. `--policy` chooses the
  preset whose gate values are written, `--action-ref` pins the tool version, `--dry-run`
  prints without writing, `--force` overwrites.

  Four behaviours are deliberate rather than convenient:

  - **The generated config is validated before it is written**, through the same loader a
    scan uses. A scaffold the tool rejects is worse than no scaffold.
  - **A refusal writes nothing.** Existing targets mean exit `2` and an untouched directory,
    never a half-scaffolded project.
  - **An existing configuration is never shadowed silently.** `vexar-mcp.toml` wins over
    `.mcp-doctor.toml` and pyproject tables, so writing one beside an existing config would
    change how the project is scanned without saying so. `init` names the file and refuses.
  - **`ignore` and `exclude` are generated empty.** A scaffold that pre-silences findings
    produces a clean report, not a clean server.

  Generated values are read from the live policy preset and `ScanConfig` defaults, so
  regenerating after a policy change cannot produce comments that disagree with the tool.

### Fixed

- **Documentation quoted a health score the tool no longer produced.** Four files showed
  `8/100` for the bundled insecure example after `MCP015` was added; it is `4/100`. A new
  test derives the real scores and fails if a documented one disagrees, so the next rule
  change cannot leave the onboarding docs claiming stale output.
- `docs/usage.md` described five report formats and omitted `html`; the command synopsis now
  lists `init`, and every subcommand has a reference section (both guarded by tests).


---

## [0.2.3] - 2026-10-28

One new rule, contributed against [issue #5](https://github.com/RANJITHROSAN17/Vexar-MCP/issues/5).
A patch release: existing rules, exit codes, report fields and policy names are unchanged.

### Added

- **Rule `MCP015` (`undocumented-parameter`, INFO / schema).** Flags a tool whose parameters
  carry no description, so the model has to guess what an argument means. `MCP001` covers the
  tool's own description; nothing covered its arguments until now.

  Three deliberate choices:

  - **One finding per tool**, listing every undocumented parameter (capped at five named plus a
    count), because a schema with eight bare parameters is one documentation problem, not eight.
  - **Silent below two parameters.** With a single argument there is nothing to disambiguate and
    firing there is noise with no diagnostic value.
  - **INFO, not WARNING.** A missing parameter description degrades tool selection; it is not a
    defect that should fail a default gate.

  No loader work was needed: all three already record per-parameter descriptions (Python
  docstring `Args:` sections and Pydantic field descriptions, JSON Schema `description`,
  TypeScript schema literals), so the rule reads data that was being collected and discarded.

### Notes

- `docs/rules.md` gains an `MCP015` section and index row; the rule total is now fourteen
  static rules plus `MCP012` (live only).
- The README demo animation was regenerated: the bundled insecure example now trips `MCP015`,
  and the asset is generated from real output, so it must change when the output does.

---

## [0.2.2] - 2026-10-28

The protocol-currency release, from a full audit against the MCP specification and the official
security guidance. Two behavioural defects, both found by attacking the product rather than
reading it. See [`docs/review-2026-10.md`](docs/review-2026-10.md) for the findings register,
the compliance matrix and the release verdict.

### Fixed

- **The client could not audit any server on the current protocol revision (High).** The
  `2026-07-28` revision removed the `initialize` handshake: the version travels per request in
  `params._meta["io.modelcontextprotocol/protocolVersion"]` and capabilities come from the
  mandatory `server/discover` RPC. Vexar-MCP sent `initialize` unconditionally, so every
  spec-current server was un-auditable and the failure read as `server rejected 'initialize'`.
  The client now tries the handshake and falls back to discovery, selecting the newest mutually
  supported revision, and reports which mode it used. `SUPPORTED_PROTOCOL_VERSIONS` gains
  `2025-11-25` (previously reported as unsupported) and `2026-07-28`.
- **Untrusted text could rewrite the report (Medium).** Tool names and descriptions from a
  scanned file — or from a remote server during a live audit — reached the output sinks
  unsanitized. ANSI escapes and bells could corrupt a terminal and a CI log; Markdown fences,
  headings and `::error::` workflow commands could be injected into a PR comment. Not code
  execution: *content spoofing*, which for a security tool is the same failure. New
  `mcp_doctor/sanitize.py` strips control and bidi characters everywhere and neutralises
  structural markers where they matter; JSON and SARIF keep the original text because their
  encoders already handle escaping.
- CRLF in annotation messages is now normalised to LF before escaping, so GitHub annotations are
  byte-identical on Windows and Linux runners (`%0A` rather than `%0D%0A`). An existing test
  asserted the old form; the assertion was updated with the reason recorded in the test.

### Added

- `docs/compliance.md`: a standing per-requirement matrix with the evidence for each row, and an
  explicit **not-applicable** section for server-side requirements (OAuth, RBAC, HTTP transport,
  HITL, rate limiting) that this architecture does not have — including why each is N/A rather
  than claimed.
- `docs/review-2026-10.md`: executive assessment, findings register with reproduction, threat
  model, verification table, deferred work, accepted risks and a release verdict.
- `tests/fixtures/stateless_mcp_server.py`: a mock server implementing the stateless revision,
  with flags for each failure mode (no discovery, pinned to one revision).
- CI gates: "Audit a server on the current protocol revision" and the sanitizer regression suite.
- `vexar-mcp audit-server` now reports `Live protocol: <revision> | negotiation: <mode>` in the
  terminal and `"live": {"protocol_version", "negotiation"}` in JSON.
- README: a `mermaid` pipeline diagram showing the untrusted-input path and the `ParseIssue`
  branch, a per-revision protocol support table, and a "Where this is going" plan.
- Roadmap: RM-032 (Streamable HTTP auditing), RM-033 (symlink confinement, review finding F-05),
  RM-034 (automated compliance matrix).

### Security

- 45 new tests: 29 for sink hardening, 16 for protocol negotiation and report provenance.

### Fixed

- **`docs/rules.md` claimed `MCP012` was unimplemented** while documenting it thirty lines
  later, and omitted `MCP014` entirely. Both corrected; `MCP014` now has a full section.
- **Every rule link in the `docs/rules.md` index was dead.** The table linked to `#mcp001` while
  the headings slugify to `#mcp001--missing-tool-description`. Links are now derived from the
  headings and verified by `scripts/check_docs.py`.
- **`docs/WEEKLY_UPGRADE_PLAN.md` cited three files that never existed** (`docs/POLICY_ENGINE.md`,
  `tests/test_risk_score.py`, `tests/test_protocol_009.py`). It is marked historical, so the
  references stay, but each is now annotated with what actually shipped instead.

- **The stale bot did nothing.** `stale-bot.yml` was a scheduled job whose only step echoed a
  string, while `CONTRIBUTOR_RULES.md` claimed stale management was active. It now nudges
  after 60 days, never closes anything, and exempts pinned/roadmap/help-wanted/
  good-first-issue/security work.
- **PyPI trusted publishing could never have worked.** `publish-pypi.yml` requested
  `id-token: write` and then ran `twine upload`, which cannot exchange an OIDC token for an
  upload permission, and no token was provided. It now builds and verifies the wheel (package
  contents, console-script metadata, version matches the tag) and publishes with
  `pypa/gh-action-pypi-publish`. RM-023 was therefore *not* shipped when the roadmap said so.
- The welcome bot greeted first-time contributors to the pre-rename name (`mcp-doctor`) and
  linked only the governance file. It now links the starter issues, the verification command,
  and Discussions.
- `CONTRIBUTOR_RULES.md` claimed the project had no welcome bot while one was running. R8 now
  documents the real bot inventory, with the two limits that matter: a bot may speak once, and
  **a bot may never close a contributor's thread or guess a difficulty label**.

### Added

- **An animated demo generated from real output.** `assets/demo.svg` is produced by
  `scripts/make_demo_svg.py`, which runs the scanner, captures stdout, and renders two scenes: a
  scan that finds real problems, and a scan that refuses to grade a file it could not parse.
  CI runs it with `--check`, so the README cannot show behaviour this version does not have.
  Pure CSS animation, no script and no external assets, with a `prefers-reduced-motion` path and
  a static fallback that is readable when animation is stripped.
- **`assets/social-preview.png`** (1280x640), composed by `scripts/make_social_preview.py` from
  the same terminal capture. This is the image every link unfurl shows. GitHub has no API for
  it, so uploading it under *Settings → Social preview* remains a manual step, documented in
  [`docs/first-impression.md`](docs/first-impression.md).
- **`QUICKSTART.md`**: a guided four-step on-ramp with the output to expect at each step, a
  table of every exit code and what it means, and a "nothing was found, why?" section. A test
  asserts every subcommand it mentions exists and that it quotes the current version.
- **`scripts/check_docs.py`**: resolves every relative link and `#anchor` across all Markdown in
  CI, including GitHub's exact slug rules and `<a id>` anchors. On first run it found 20 real
  problems, below.
- **`docs/first-impression.md`**: the design constraints of the first sixty seconds, the
  patterns borrowed from fast-growing projects and why, and an honest note that a good README
  raises conversion but does not create the traffic that trending measures.
- README restructured: the demo and a plain-language **What this is** now sit above the fold,
  the quick start is three commands instead of a list, and adoption, Actions and "not sure where
  to point it" moved into collapsed sections.

- **`issue-labeler.yml`** with `.github/scripts/auto_label_issue.py`: routes a new issue to
  `area/*` and `type/*` from its text and paths. Deliberately refuses to apply `difficulty/*`
  or `good first issue` — an automated guess at "beginner friendly" is how a starter issue
  becomes a dead end. Pure function, 14 tests.
- README "Easy wins" table and a contributor landing paragraph, so a first-time visitor sees
  four tasks they can finish today instead of a wall of feature documentation.
- **A guaranteed beginner on-ramp.** The curator bot now tracks
  `min_open_good_first_issues` (default 2) separately from `max_open_roadmap_issues`. The queue
  ceiling protects the maintainer; the starter floor protects the newcomer, and is allowed to
  exceed the ceiling because a beginner who finds nothing finishable leaves silently, while a
  maintainer two issues over budget notices immediately. Added RM-031 (a worked CI example) so
  the floor is fillable with genuinely easy work rather than a stretched "hard" label.

---

## [0.2.1] - 2026-10-28

The trust release. 0.2.0 added policy, baselines and live auditing on top of a scanner that
could still report `100/100 (A+)` and exit `0` for a file it had failed to parse. That class
of false confidence is now closed by construction, pinned by regression tests, and gated in
CI.

### Fixed

- **Fail-open analysis (P0).** A Python syntax error or malformed JSON produced zero tools,
  which produced zero findings, which produced a perfect score and a passing exit code.
  Loaders now report `ParseIssue`s instead of swallowing errors; a scan that could not read
  its inputs is never graded.
- **Server-wide token budget was configured but never enforced.** `max-server-tokens` is now
  enforced by rule **`MCP014`**, which names the total, the budget, and the largest
  contributor tools. Covered below, exactly-at, and above the limit.
- **`files_scanned` counted the wrong thing** - it counted files *containing tools* rather
  than files *analyzed*, so a scan of forty parseable Python files could report `0`.
- **`@register_rule` did not reject duplicate rule IDs.** Programmatic registration was
  guarded; the decorator silently replaced an existing rule. Both now share one guard.
- **Excluded directories were traversed, not pruned.** Discovery called `rglob("*")` and
  filtered afterwards, so scanning a monorepo walked every file under `node_modules`. The
  walk is now pruned during traversal, and always skips VCS/cache directories even when a
  project replaces the default exclude list. A 10,000-file fixture with 50 relevant files
  discovers in about 4 ms.
- **The composite action scanned the repository up to four times**, once per output format,
  so reports could disagree and CI paid for discovery, parsing and rules repeatedly. All
  artefacts now come from one analysis via `--emit`.
- The history sparkline could record an ungraded score (`None`) and poison the trend for
  later runs; only graded runs are recorded now.
- **Dead CLI stub removed.** `mcp_doctor/cli_simulate.py` was unreachable from the real
  entry point, 0% covered, and printed a hardcoded token estimate for a hardcoded example.
  Now that `simulate` is a real command reading a real scan, the stub is gone rather than
  left behind as a second, wrong answer.

### Added

- **Scan status** (`clean`, `findings`, `partial`, `no_matches`, `error`) and **coverage**
  counters (`candidate_files`, `files_analyzed`, `files_failed`, `files_with_tools`,
  `tools_analyzed`), reported in terminal, JSON, Markdown, HTML and SARIF.
- **`ParseIssue`** reporting with kind (`syntax_error`, `invalid_json`, `unreadable`), file
  and line, surfaced in every reporter and in SARIF `invocations.toolExecutionNotifications`
  as `executionSuccessful: false`. Never counted as a finding.
- **Exit codes `3`** (incomplete analysis) and **`4`** (no supported tool found when
  `--fail-on-no-matches` is set). `0`/`1`/`2` are unchanged.
- **`--emit FORMAT=PATH`** (repeatable) renders several formats from one scan;
  `--allow-incomplete` deliberately downgrades the completeness gate; `--fail-on-no-matches`
  requires a non-empty result.
- **`score` is now `int | None`.** It is `null`/`n/a` whenever the scan was not graded, so
  "we measured nothing" cannot be mistaken for `100`. `grade` is `"N/A"` in the same cases.
- Composite action inputs `allow-incomplete` and outputs `status`, `analysis-complete`,
  `files-failed`. With `--emit`, `score` is emitted empty for ungraded scans.
- **`vexar-mcp simulate`** projects a catalog into per-call and monthly spend for a chosen
  model and call volume, built on the same estimator that drives `MCP002`/`MCP014` so a
  projection cannot contradict a finding. An unknown model is an error (exit `2`), never a
  default price; an incomplete scan is labelled a lower bound (exit `3`); an empty catalog
  exits `4` instead of quoting a confident `$0.00`. See
  [docs/usage.md](docs/usage.md).
- `docs/scan-status.md` and new CI gates asserting the fail-open regression and the server
  budget, plus 45 tests covering completeness, coverage, pruning, multi-output and registry
  integrity.

### Changed

- Terminal, Markdown and HTML reports no longer print "All checks passed" or "Zero issues
  detected" for a scan that was not complete; they state that the result is not clean.
- `summary.files_scanned` now means files analyzed (see Fixed).

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
- `code-review-bot.yml`: replaced a free-text write to `$GITHUB_OUTPUT` (GitHub expects
  `key=value`) and stopped it commenting on push events, where there is no issue to
  comment on (`HttpError: Not Found`).
- `ai-code-review-bot.yml`: the inline `python -c` report generator had broken quote
  escaping and died with `SyntaxError: unterminated string literal`; it now lives in
  `.github/scripts/ai_review_report.py` and is tested. Also fixed a call to `vexar-mcp
  audit` (no such command), a read of `summary.info` (the key is `infos`), and a commit
  comment that needed `contents: write` — push runs now publish to the job summary.
- `final-merge-approval-bot.yml`: the job condition ended with `|| github.event_name ==
  "push"` for an event the workflow never subscribes to, and `workflow_dispatch` runs
  tried to comment with no issue in context. Both fixed.
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
