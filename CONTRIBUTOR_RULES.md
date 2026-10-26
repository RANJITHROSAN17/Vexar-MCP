# RANJITHROSAN17 Organization Contributor Rules — Corporate Standard

> Single source of truth for all repositories. Enforced via `.github/rulesets/contributor-enforcement.json` (branch protection, required checks, linear history). No exceptions.

| # | Rule | Enforcement |
|---|---|---|
| R1 | **Conventional Commits** — PR titles `feat/fix/docs/refactor/perf/test/chore/ci(...)` | `pr-governance.yml` bot rejects non-compliant titles |
| R2 | **Quality Gate** — `ruff`, `mypy`, `pytest --cov-fail-under=85` pass | `ci.yml` required checks; merge blocked if any fail |
| R3 | **One Change / One PR** — max 10 changed files per PR | `pr-governance.yml` flags `size/XL`; maintainers enforce |
| R4 | **Docstrings + Types** — all public APIs must have Google-style docstrings and type hints | `mypy --strict`; bot comments if missing |
| R5 | **No Secrets** — zero hardcoded keys / tokens in code | `mcp-doctor` `MCP009` rule; `pr-governance.yml` checks `.env` not committed |
| R6 | **Structured Issues** — use `.github/ISSUE_TEMPLATE/` only | Auto-close blank issues via `pr-governance.yml` |
| R7 | **Code Owners** — `.github/CODEOWNERS` required for all changed paths | Branch protection requires review by listed owner |
| R8 | **Bot Silence** — only 3 bots notify; no welcome/stale spam | Configured in `.github/workflows/*.yml` + `dependabot.yml` |

---

## For External Contributors (Good First Issue / Help Wanted)

1. Read `CONTRIBUTOR_RULES.md` → this file.
2. Pick an open Issue (`#1`–`#8` for `mcp-doctor`; similar queued for your repo).
3. Create a branch: `feat/your-issue-id-desc` (e.g., `feat/mcp009-hardcoded-keys`).
4. Make your change + add/update tests in `tests/`.
5. Open PR with structured description linking Issue (`Closes #N`).
6. Ensure all 4 CI bots pass (see `ci.yml` required checks list).
7. Wait for 1 approving review from `CODEOWNERS`; no self-merge on protected files.

---

## For Maintainers (RANJITHROSAN17)

- Monitor only **3 notification sources**: `pr-governance.yml`, `roadmap-issue-bot.yml`, `release.yml`.
- Dependabot updates are grouped (`groups: python-dependencies`, `github-actions`) — merge weekly, not per-PR.
- New repositories must copy `.github/CODEOWNERS`, `CONTRIBUTOR_RULES.md`, `.github/rulesets/contributor-enforcement.json`, and the 3-bot workflow set from `mcp-doctor`.
