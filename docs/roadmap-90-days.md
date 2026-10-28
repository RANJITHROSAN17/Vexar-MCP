# 90-day roadmap

**Verification first.** Every line below was checked against this repository at
`dd4c07b` before being scheduled. The large organisational plan for a scanner like this —
define the contract, document exit codes, ship policy presets, add baselines, write a
limitations page — is **mostly already done here**, and pretending otherwise would waste the
next ninety days. What follows is the residue: the work that is actually missing.

Companion: [`roadmap-6-month.md`](roadmap-6-month.md) for the longer arc, and
[`../../vexar-autofix/ROADMAP.md`](https://github.com/RANJITHROSAN17/vexar-autofix/blob/main/ROADMAP.md)
for the child product.

---

## Where it actually stands

| Capability | Status | Evidence |
|---|---|---|
| Core contract: what a finding, a pass and a failure mean | **done** | [`scan-status.md`](scan-status.md), [`governance.md`](governance.md) §"Exit codes are API" |
| Exit codes documented and stable (`0` pass, `1` gate, `2` config/scan error, `3` …) | **done** | [`policies.md`](policies.md) §Exit codes, [`github-actions.md`](github-actions.md) |
| Partial / incomplete analysis behaviour | **done** | [`scan-status.md`](scan-status.md); `test_analysis_completeness.py` reports analysis failures as SARIF notifications |
| Policy presets | **done** | `default`, `ci`, `strict-prod`, `enterprise-governance` in `policies.py`, selectable in `action.yml` |
| Baselines (adopt current, fail on new only) | **done** | [`baseline.md`](baseline.md); malformed baselines fail closed with exit `2` |
| SARIF reporter | **done** | `reporters/sarif.py`, exercised in `test_analysis_completeness.py` |
| GitHub Action with step outputs | **done** | `action.yml`: `score`, `grade`, `risk-index`, `new-findings`, `policy`, `exit-code`, step summary |
| Known limitations, written honestly | **done** | [`limitations.md`](limitations.md) |
| Test suite | **done** | 404 tests |
| **JSON Schema for machine contracts** | **missing** | no `*.schema.json` in the tree |
| **SARIF validated against the official schema** | **partial** | outputs are tested for content, not schema conformance |
| **Benchmark corpus with expected findings** | **partial** | `examples/` holds 3 servers + 2 manifests; the plan asked for 5–10 vulnerable + 5 clean |
| **PR annotations** | **missing** | step summary exists, inline annotations do not |
| **Release discipline** | **drift** | `pyproject.toml` is `0.2.6`; latest published release is `v0.2.1` |
| **PyPI availability** | **blocked** | not published; see D1 — the import name is taken |

---

## The gaps

### D1 — The import name is already owned by someone else · **P0, blocks PyPI**

**Verified.** `mcp-doctor` on PyPI is a different project by a different author
(`Jiansen/mcp-doctor`, v0.1.0). This repository's distribution is named `Vexar-MCP` but its
**import package is `mcp_doctor`**. Publishing would place a second, unrelated `mcp_doctor/`
into site-packages and shadow or be shadowed by the existing distribution depending on
install order.

The child product imports it directly (`from mcp_doctor import ScanConfig, scan_paths`), so
this cannot be fixed in one repo.

**Do, in this order:**

1. Rename `src/mcp_doctor/` → `src/vexar_mcp/`, keeping a `mcp_doctor` shim module that
   re-exports with a deprecation warning, so nobody's code breaks on the day of the change.
2. Bump the pinned commit in `vexar-autofix` and update its two import sites.
3. Publish `vexar-mcp` to PyPI under trusted publishing (issue **#50** already tracks
   proving the pipeline end to end).

**Done when:** `pip install vexar-mcp` and `pip install vexar-autofix` coexist with no file
overlap, and `import mcp_doctor` still works through the shim for one minor version.

### D2 — Machine contracts have no schema · P1, weeks 2–3

The JSON report, the baseline file and the policy config are all parsed by third parties
(CI jobs, the child product, dashboards) and none of them is published as a schema.

**Do:** `schemas/report.schema.json`, `baseline.schema.json`, `policy.schema.json`, plus a
test that validates real output against each, and a `--validate` path for hand-edited
baselines. **Done when:** a consumer can validate our output without importing our code.

### D3 — SARIF conformance, not just SARIF output · P1, week 3

**Do:** validate emitted SARIF against the official 2.1.0 schema in CI, including the
`notifications` we emit for analysis failures. **Done when:** a malformed SARIF file fails
the build.

### D4 — Benchmark corpus and public scorecard · P1–P3

Current corpus is 3 servers. The plan asks for 5–10 vulnerable and 5 clean with declared
expected findings — the same discipline the child product's eval already applies.

**Do:** `benchmarks/` with per-fixture `expected.json` (exact rule sets, not subsets), five
new vulnerable servers covering each rule family, five clean servers including a
deliberately hard negative (a helper that *looks* like a sink but is confined), a partial-
parse case, a high-token case and a schema-error case. Then a generated
A generated scorecard, published with the release (not hand-written): rule coverage, detection examples, false-positive behaviour,
and what is intentionally not detected. **Done when:** the scorecard is generated from the
corpus in CI and cannot drift from it.

### D5 — Release discipline · P1, week 4

`pyproject.toml` says `0.2.6`; the newest release is `v0.2.1`. Five version bumps are
unpublished, so "latest release" is a lie in every badge and install snippet.

**Do:** tag going forward, one release per merged milestone, release notes from the
CHANGELOG, and a CI check that fails when `pyproject.toml`'s version has no matching tag.
**Done when:** the badge, the changelog and the package agree.

### D6 — PR annotations · P2, weeks 5–6

**Do:** emit `::error file=…,line=…,col=…::` workflow commands for `fail-on` severities so
findings appear on the diff instead of only in a step summary. Behind an input, default on
for `ci` and `strict-prod`. **Done when:** a pull request shows the finding on the offending
line.

### D7 — Two real-world adoption write-ups · P2, week 7

**Do:** scan an external MCP server repository and publish the outcome, including anything
the scanner got wrong; and a baseline-adoption walkthrough in a PR flow (issue **#56**
already covers the second). **Done when:** two documents exist that someone else could follow
verbatim.

### D8 — Consolidated "supported inputs" page · P3, week 8

`ARCHITECTURE.md` and `github-actions.md` are good; there is no single page saying what the
scanner accepts (Python AST, TypeScript loader limits, manifests), what it silently skips, and
what that means for the score. **Done when:** [`scan-status.md`](scan-status.md) gains a
"supported inputs and their limits" section, or a sibling page does.

---

## The 90 days

| Days | Focus | Exit criterion |
|---|---|---|
| 1–15 | **D1** rename + shim, **D5** version/tag alignment | `import mcp_doctor` still works; badge matches package |
| 16–30 | **D2** schemas, **D3** SARIF conformance | Third parties can validate our output without our code |
| 31–45 | **D4** corpus part 1: five vulnerable, five clean, `expected.json` | Corpus runs in CI |
| 46–60 | **D6** PR annotations, **D7** two adoption write-ups | A PR shows inline findings; two guides exist |
| 61–75 | **D4** part 2: partial-parse, high-token, schema-error cases; **D8** supported inputs | Scorecard generated, not written |
| 76–90 | **D1** step 3: publish to PyPI (issue #50), release `v0.3.0` | `pip install vexar-mcp` works and the child product pins it from PyPI |

**Not in these 90 days** (already tracked as issues and deliberately deferred): registry-file
multi-server auditing (**#51**), organisation policy inheritance (**#52**), tree-sitter
TypeScript backend (**#53**), token-estimator accuracy benchmark (**#63**). These are real
work; they are simply not what makes the product trustworthy this quarter.

---

## Invariants

New rules and new presets must not break these:

1. Exit codes are API: `0` pass, `1` gate failure, `2` invocation/config error. Documented
   in [`governance.md`](governance.md).
2. A malformed or newer-schema baseline fails closed, never open.
3. An incomplete scan never reports as a pass — it is surfaced as an analysis notification.
4. The score is reproducible for a given file and rule-pack version.
5. A preset may only ever be *stricter* than `default`.

## How to tell whether this is working

Not star count. Two numbers: **time from clone to first meaningful scan on a fresh machine**,
and **the false-positive rate on the clean corpus**. The first is adoption friction, the second
is the only reason anyone keeps a security scanner installed.
