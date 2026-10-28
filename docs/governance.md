# Repository Governance and Maintenance Guide

## Purpose

This document defines how Vexar-MCP is maintained, reviewed, released, and governed as it grows from a strong technical prototype into a production-quality platform for MCP security and governance.

The goal is to preserve engineering quality, contributor trust, and product clarity without introducing over-bureaucracy.

## Governance model

### Maintainer ownership

- The repository is currently owned by the primary maintainer at `@RANJITHROSAN17`.
- The CODEOWNERS file defines review and ownership expectations for repository-level changes.
- Ownership is expected to expand as the project matures and contributions increase.

### Review policy

All non-trivial changes must follow this flow:

1. Open or reference a GitHub issue or roadmap item.
2. Keep the scope small and understandable.
3. Update tests and docs where behavior or configuration changes.
4. Follow Conventional Commits in the PR title.
5. Ensure CI passes before merge.
6. Explicitly call out risks, compatibility concerns, and follow-up items.

### Change categories

- Bug fixes: must include regression tests and risk notes.
- Rule additions: must include documentation and test coverage.
- Parser/loaders changes: must include fixture-based coverage for edge cases.
- Reporter changes: must validate output and maintain readability.
- Governance and release changes: must be reviewed by the maintainer or designated owner.

### Decision making

- For day-to-day engineering decisions, the maintainer approves changes.
- For security-sensitive changes, maintainers should treat the repo as requiring stricter, more explicit review.
- For roadmap-level product decisions, issues and roadmap items should drive implementation.

## Contribution standards

### Required standards

Every PR should:

- be tied to an issue, roadmap item, or clearly stated need
- include tests when behavior changes
- avoid broad refactors without justification
- document user-visible behavior changes
- maintain compatibility with existing flows where practical

### Quality bar

The repository expects the following before merge:

- code formatting is clean
- lint checks pass
- type checks pass
- tests pass with coverage maintained
- public behavior is documented or obvious from examples

## Security and risk handling

### Security policy

- Security-relevant issues must be handled with care and should not be disclosed publicly.
- Use the repository security reporting workflow and private vulnerability reporting channels when applicable.
- Security issues must be triaged with a clear risk assessment and release impact note.

### Risk review requirements

Any change with product, policy, or security implications should have:

- a clear rationale
- scope description
- compatibility impact note
- fallback/rollback plan when relevant

## Release governance

See `docs/release-management.md` for release process and versioning.

At a high level:

- semantic versioning is required
- release notes should document notable user-facing behavior changes
- breaking changes require migration guidance
- package and workflow release flows should be reviewed before publication

## Roadmap governance

- Roadmap items live in `.github/roadmap.json`.
- Roadmap scope should remain product-oriented, not purely issue spam.
- Items should be labeled by type, area, and effort.
- High-impact product or policy work must be tied to a clear value proposition.

## Maintenance expectations

### Repository health checks

Maintainers should review:

- CI status
- dependency freshness
- stale issues and PRs
- missing documentation for user-visible changes
- runtime and packaging compatibility
- backlog quality and issue hygiene

### Repository automation hygiene

- Workflow and composite-action manifests are validated in CI
  (`.github/scripts/validate_workflows.py`), including YAML validity and permission-scope
  names. An invalid workflow is reported by GitHub as a failed run with **no jobs**, which
  is easy to mistake for a bot malfunction and then ignore; the linter turns that into a
  normal failed check with a file and line number.
- A workflow that fails on every push is a governance problem, not background noise: it
  trains maintainers to ignore the Actions tab. Fix or disable it.

### Documentation hygiene

User-facing changes should update at least one of the following when relevant:

- README.md
- docs/
- CONTRIBUTING.md
- SECURITY.md
- roadmap or governance notes

## Lifecycle guidance

### Growing beyond a single maintainer

As contribution volume grows, move toward:

- designated reviewers for rules, parsers, reporters, and docs
- a release reviewer model
- a clearer policy for approving high-risk changes
- a structured issue triage process

### Avoiding governance bloat

This repository should remain lightweight and practical. The governance model should stay efficient:

- no unnecessary approval layers
- no excessive process overhead
- strong docs and issue quality instead of heavy ceremony
- explicit risk-based review where it matters

## Policy and rule governance (0.2.0)

The tool now enforces standards, not just reports findings, so policy changes carry more
weight than feature changes.

### Rule ID namespace

- `MCP001`–`MCP013` are owned by the project. `MCP012` is reserved for live-only protocol
  findings.
- Retired IDs are never reused: a retired ID stays retired, because a reused ID silently
  changes the meaning of someone's committed baseline.
- Third-party rule packs must use their own prefix and cannot register `MCP*` IDs. The
  registry rejects collisions and duplicate slugs, so a pack can never shadow a built-in
  check.

### The trust contract (0.2.1)

These are permanment guarantees, and a change to any of them is a security-relevant change:

1. **A scan that could not read its inputs is never graded and never passes.** Loaders report
   `ParseIssue`s; the engine withholds the score; the CLI exits `3`.
2. **Findings and analysis status are separate.** A parse error is never emitted as a
   security finding, and an empty findings list on an incomplete scan means nothing.
3. **Exit codes are API.** `0` pass, `1` gate failure, `2` invocation/config error, `3`
   incomplete analysis, `4` no matches. Renumbering them is a breaking change.
4. **Excluded directories are pruned, never traversed.** Scanning must not pay for code it
   will not read.
5. **One analysis, many renderings.** Reports for a single run must describe the same
   snapshot; adding a format must not add a scan.

Any PR that weakens one of these — for example by making the completeness gate opt-in, or by
restoring a numeric default score for empty scans — must be reviewed as a security regression.

### The roadmap is reviewed JSON, and the bot obeys it

`.github/roadmap.json` is the single source of truth for the contributor queue. The curator
bot never invents work and never trusts its own memory of the repo:

- an item with `"status": "shipped"` is never opened, and its open issue is closed with the
  release it landed in;
- an open issue whose title or body drifted from the manifest is rewritten, so a contributor
  never reads text the project no longer believes;
- a queued item may *mention* an existing rule, but may never *propose* an ID that is already
  registered — otherwise contributors are asked to rebuild shipped work under a taken ID.

Three tests enforce this (`tests/test_roadmap_bot.py`): no proposal of an existing rule ID,
every `shipped_in` present in `CHANGELOG.md`, and every queued item carrying files and
acceptance criteria. Shipping a feature is therefore a small, reviewable diff: set
`status`/`shipped_in`, add the changelog entry, push, and let the bot close the issue.

The queue has **two independent budgets**, and they exist for different people:

| Key | Protects | Behaviour |
| :--- | :--- | :--- |
| `max_open_roadmap_issues` | the maintainer | ceiling on open work to triage |
| `min_open_good_first_issues` | the newcomer | floor for beginner-friendly work; may push the queue past the ceiling |

The floor is allowed to break the ceiling on purpose. A newcomer who finds nothing they can
finish does not come back, and that loss is silent — no metric records it. A maintainer who is
briefly two issues over budget notices immediately.

This exists because it went wrong. Before 0.2.1, nineteen of thirty items were marked queued
while the code was on `main`; five issues stayed open advertising delivered work, and two
proposed rule IDs that other rules had already claimed.

### Policy presets are a versioned contract

| Change | Required |
| :--- | :--- |
| Add a preset | Docs + changelog (additive, safe) |
| Tighten a preset | Changelog "Policy changes" note + rationale in the PR |
| Loosen a preset | Reviewed as security-relevant; changelog must say what is no longer caught |
| Change a protected category | Treated as loosening or tightening accordingly; always called out |

### Baselines are accepted-risk records

- Baselines are committed artefacts and are reviewed like code.
- A baseline diff that only adds entries needs a reason in the PR description.
- Entries are never hand-edited; regenerate from a scan.
- Each accepted-risk backlog should have an owner and a target issue.

### Trust boundaries introduced by 0.2.0

Two features run code. Both are opt-in, both are documented, and both are in scope for
review:

1. **Live server auditing** (`audit-server`) starts a process the operator names. Rails:
   argv-only, single deadline, response and page caps, child killed on expiry.
2. **Python rule packs** are imported on demand. Review them like a dependency; prefer
   declarative packs, which are data and never executed.

Static `scan` remains free of code execution — that property must not regress.

## Summary

Vexar-MCP should remain a disciplined, high-trust repository: fast-moving, technically strong, documentation-aware, and release-minded. Governance should help the project scale without turning into bureaucracy.

The project is strongest when it balances:

- contributor velocity
- code quality
- security awareness
- product clarity
- release discipline

This is the right foundation for evolving from a high-quality technical project into a platform-grade MCP security product. As the tool started enforcing standards in 0.2.0, the bar shifted from "does it detect well?" to "can teams trust what it enforces, and can they audit why a build failed?" Every governance decision above serves that second question.
