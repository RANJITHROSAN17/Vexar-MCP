# Release and Maintenance Management

## Release philosophy

Vexar-MCP should ship in a predictable, controlled, and user-friendly way. The release process exists to preserve trust, reduce breakage, and make upgrades obvious for users and contributors.

## Versioning model

Use semantic versioning:

- MAJOR: breaking changes or incompatible user-facing behavior
- MINOR: new features, new rules, or additive capability
- PATCH: bug fixes, documentation improvements, and low-risk maintenance

## Release process

### Normal release flow

1. Feature work is merged into the default branch after review and CI pass.
2. The maintainer evaluates whether the change set warrants a release.
3. Release notes summarize user-visible improvements, fixes, and compatibility notes.
4. The package and workflow artifacts are published only after validation.
5. A release tag is created with a changelog note and relevant links.

### Required release notes

Every release should include:

- user-facing changes
- compatibility changes or migration notes
- known limitations or workarounds
- testing status and support matrix
- security-related notes if applicable

## Maintenance expectations

### CI gate before merge

The project should maintain the following flow consistently:

- lint checks pass
- format checks pass
- type checks pass
- unit tests pass
- coverage remains above the defined threshold
- no new high-risk issues are introduced without documentation

### Dependency maintenance

- Keep dependencies current and reviewed.
- Dependabot or equivalent review automation should be used consistently.
- Security-sensitive updates should be prioritized.
- Breaking dependency updates must be reviewed carefully and documented.

### Repository hygiene

The maintainer should periodically review:

- stale issues
- stale PRs
- roadmap alignment
- untriaged bug reports
- duplicates and low-signal issues
- missing documentation for user-visible behavior changes

## Maintenance policy

### Safe merge policy

A change should be merged only when:

- there is clear issue or roadmap alignment
- tests cover the changed behavior
- docs or examples are updated where necessary
- risk is understood and documented
- the PR remains focused on the intended change

### Rollback policy

For a change with product or security impact:

- preserve a clear rollback path
- record what changed and why
- identify which users will be affected
- document any migration steps clearly

## Support and compatibility

### Supported runtime assumptions

- Python versions must be explicitly listed and validated
- documented minimum requirements should match CI coverage
- runtime assumptions should remain conservative

### User communication

When there are breaking changes or notable behavior changes:

- update release notes
- document migration steps
- clearly flag compatibility changes in PRs and release summaries

## Summary

This project should remain disciplined and predictable: clear versioning, stable release hygiene, and maintenance discipline are part of product maturity. The release process should protect trust without adding unnecessary ceremony.

The project is strongest when it combines:

- low-friction development for contributors
- strong quality gates
- transparent change communication
- steady product evolution without breakage

---

## Release 0.2.0 — what changed in the process

0.2.0 is the first release where the process below is enforced by machinery rather than by
intention:

| Control | How it is enforced |
| :--- | :--- |
| Additive-only report fields | `schema` keys `vexar-mcp-report/v1` and `vexar-mcp-baseline/v1`, documented contract in `CHANGELOG.md` |
| No accidental gate weakening | Protected categories in `policies.py`; loosening a preset requires a changelog note and security review |
| Behaviour changes are visible | CI matrix (3.10–3.13) runs lint, format, `mypy --strict`, tests with an 85% coverage gate, and warnings-as-errors |
| The product actually works | Product dogfood jobs gate a clean server at `strict-prod`, exercise the baseline flow, assert protected rules cannot be ignored, run a live audit against a fixture server, load a rule pack, and assert exit code `2` for configuration errors |
| Release artefacts are inspectable | CI uploads SARIF and the HTML report from the dogfood run |

### Release checklist

1. `CHANGELOG.md` has a section for the version, with any "Policy changes" note.
2. `pyproject.toml` and `src/mcp_doctor/__init__.py` versions match.
3. CI green on all supported Python versions.
4. Dogfood jobs green — including the intentional exit-`1` assertion.
5. Tag `vX.Y.Z`, publish, and verify `pip install` plus `vexar-mcp --version` in a clean
   environment.
