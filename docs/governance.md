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

## Summary

Vexar-MCP should remain a disciplined, high-trust repository: fast-moving, technically strong, documentation-aware, and release-minded. Governance should help the project scale without turning into bureaucracy.

The project is strongest when it balances:

- contributor velocity
- code quality
- security awareness
- product clarity
- release discipline

This is the right foundation for evolving from a high-quality technical project into a platform-grade MCP security product.
