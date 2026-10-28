# RANJITHROSAN17 Organization Contributor Rules — Corporate Standard

> Single source of truth for all repositories. Enforced via `.github/rulesets/contributor-enforcement.json` (branch protection, required checks, linear history). No exceptions.

| # | Rule | Enforcement |
|---|---|---|
| R1 | **Conventional Commits** — PR titles `feat/fix/docs/refactor/perf/test/chore/ci(...)` | `pr-governance.yml` bot rejects non-compliant titles |
| R2 | **Quality Gate** — `ruff`, `mypy`, `pytest --cov-fail-under=85` pass | `ci.yml` required checks; merge blocked if any fail |
| R3 | **One Change / One PR** — max 10 changed files per PR | `pr-governance.yml` flags `size/XL`; maintainers enforce |
| R4 | **Docstrings + Types** — all public APIs must have Google-style docstrings and type hints | `mypy --strict`; bot comments if missing |
| R5 | **No Secrets** — zero hardcoded keys / tokens in code | Vexar-MCP `MCP009` rule; `pr-governance.yml` checks `.env` not committed |
| R6 | **Structured Issues** — use `.github/ISSUE_TEMPLATE/` only | Auto-close blank issues via `pr-governance.yml` |
| R7 | **Code Owners** — `.github/CODEOWNERS` required for all changed paths | Branch protection requires review by listed owner |
| R8 | **Bot Silence** — a bot may comment on your work at most once, and **no bot may ever close a contributor's issue or PR** | Enforced by the workflow inventory below |

---

## For External Contributors (Good First Issue / Help Wanted)

1. Read `CONTRIBUTOR_RULES.md` → this file.
2. Pick an open Issue (`good first issue` / `help wanted`). Each one lists the files and the
   commands that prove your change works, so you can self-verify before asking for review.
3. Create a branch: `feat/your-issue-id-desc` (e.g., `feat/mcp009-hardcoded-keys`).
4. Make your change + add/update tests in `tests/`.
5. Open PR with structured description linking Issue (`Closes #N`).
6. Ensure all 4 CI bots pass (see `ci.yml` required checks list).
7. Wait for 1 approving review from `CODEOWNERS`; no self-merge on protected files.

---

## Bot inventory — what may touch your issue or PR

Three bots are *silent unless they change something*; the rest only run on maintainer
triggers. This table is the contract for R8, and it is the list to check when a workflow
starts behaving like spam.

| Workflow | When it speaks | Hard limits |
|---|---|---|
| `welcome-bot.yml` | one comment, only for `FIRST_TIMER` / `FIRST_TIME_CONTRIBUTOR` | never greets a second time (marker-checked), never greets bots or the maintainer |
| `issue-labeler.yml` | only when it adds a label | applies `area/*` and `type/*` from issue text; **never** `difficulty/*`, `good first issue`, or `help wanted` — those stay human decisions |
| `pr-governance.yml` | only when a PR breaks R1/R3/R6 | size/area labels are derived from changed paths, never from prose |
| `stale-bot.yml` | one nudge after 60 days of inactivity | `days-before-close: -1`; exempts `pinned`, `roadmap`, `help wanted`, `good first issue`, `type/security` |
| `roadmap-issue-bot.yml` | weekly, maintainer queue only | never opens an item marked `shipped`; closes a shipped item's issue with its release |

Two rules matter more than the rest:

1. **A bot never closes a contributor's thread.** Only a human closes a human's work. The
   stale bot nudges and stops there.
2. **A bot never guesses a human judgement.** Difficulty and "beginner friendly" are the
   labels that decide whether a new contributor succeeds; a bot that guesses them produces
   issues nobody can finish.

## For Maintainers (RANJITHROSAN17)

- Monitor only **3 notification sources**: `pr-governance.yml`, `roadmap-issue-bot.yml`, `release.yml`.
- Dependabot updates are grouped (`groups: python-dependencies`, `github-actions`) — merge weekly, not per-PR.
- New repositories must copy `.github/CODEOWNERS`, `CONTRIBUTOR_RULES.md`, `.github/rulesets/contributor-enforcement.json`, and the 3-bot workflow set from Vexar-MCP.
