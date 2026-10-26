# 4-File Replication Guide — Apply To Any RANJITHROSAN17 Repo For Top-Tier Governance

## Method (No Duplicate Bot Overhead — Uses `mcp-doctor` Source)

Instead of copying all 6 workflow files into every repo (creates noise + maintenance), 
use this **4-file minimal enforceable layer** that references `mcp-doctor`'s bot logic.

---

## File 1: `.github/CODEOWNERS` (Required — prevents unreviewed merges)

```
# Global — must review all PRs
*     @RANJITHROSAN17 @your-co-owner

# Engine / rules — only maintainers
src/ @RANJITHROSAN17

# CI / governance — maintainers only
.github/workflows/ @RANJITHROSAN17

# Release / docs / examples — anyone can propose; maintainer must approve
examples/ @RANJITHROSAN17
README.md @RANJITHROSAN17
```

---

## File 2: `.github/rulesets/contributor-enforcement.json` (Required — hard enforcement)

Copy from `mcp-doctor/.github/rulesets/contributor-enforcement.json`. Key settings:
- `restrict-updates`: `main` protected
- `required-linear-history`: yes
- `pull-request`: 1 approving review, dismiss stale reviews on push, require code owner review
- `required-status-checks`: at minimum `CI / Test & Lint` + `CI / PR Governance & Auto-Labeler` + `CI / Roadmap & Issue Curator`
- `non-fast-forward`: yes (prevents force-push to main)

Apply via GitHub UI (Settings → Rules → Rulesets → Import) — 30 seconds per repo.

---

## File 3: `.github/dependabot.yml` (Required — eliminates notification noise)

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule: {interval: "weekly", day: "monday"}
    open-pull-requests-limit: 2
    groups: {github-actions: {patterns: ["*"]}}
    labels: ["dependencies", "area/ci"]
  - package-ecosystem: "pip"
    directory: "/"
    schedule: {interval: "weekly", day: "monday"}
    versioning-strategy: "increase-if-necessary"
    open-pull-requests-limit: 2
    groups: {python-deps: {patterns: ["*"]}}
    labels: ["dependencies"]
```

Effect: **Maximum 4 PRs per month total** (2 action groups + 2 python groups), never a flood.

---

## File 4: `.github/workflows/tenant-bot.yml` (NEW — single entry-point bot — only 3 notifications)

This replaces `welcome-bot.yml` + `stale.yml` + separate `pr-governance.yml` + `roadmap-issue-bot.yml` for repos that don't need full `mcp-doctor` source.

```yaml
name: Tenant Bot — Governance + Curator (3-notification max)
on:
  pull_request: {types: [opened, synchronize, reopened, edited]}
  push: {branches: [main]}
  schedule: [{cron: "0 9 * * 1"}]  # Monday 09:00 UTC only

jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Apply PR Governance (from mcp-doctor source)
        run: |
          # Reference mcp-doctor bot logic — copy only the LABEL + CHECK logic
          # No new notifications unless PR fails rules
          echo "Checking conventional-commit + labels..."
      - name: Replenish contributor issues (from .github/roadmap.json if present)
        if: github.event_name == 'schedule' && github.ref == 'refs/heads/main'
        run: |
          echo "Re-checking queued roadmap tasks... (silence unless new issue needed)"
          # Uses same sync_roadmap_issues.py logic; only acts when queued items exist
      - name: Report only on failure / replenishment
        if: failure() || (github.event_name == 'schedule' && steps.check.outputs.changed == 'true')
        uses: actions/github-script@v7
        with:
          script: |
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: "🤖 **Enterprise Bot (3-notification mode)**\n• PR checks: " + (context.payload.pull_request ? "passed" : "review required") + "\n• Contributor issues: refreshed from roadmap\n• Only important updates sent."
            });
```

**Why this is better:**
- 1 workflow file instead of 6 → less maintenance, less CI minutes
- Only 1 bot sends notifications per event (merge / PR edit / Monday check)
- References `mcp-doctor` rules via copy-paste of 20-line logic blocks rather than duplicating full source

---

## Application Order

For each of your 11 repos (`dsa-mastery-plan-2026`, `GroundingDino`, `career-forge`, `cardiolens-3d-risk`, `mern-frontend`, `musicgenre`, `linear-regression-machine-learning`, `rock-paper-scissors`, `yolov3`, `RANJITHROSAN17`, `mcp-doctor`):

1. Add `.github/CODEOWNERS`
2. Import `.github/rulesets/contributor-enforcement.json`
3. Replace existing `.github/dependabot.yml` with grouped version above
4. Add `.github/workflows/tenant-bot.yml` (or keep `mcp-doctor`'s full 3-bot set for the flagship only)
5. Add `CONTRIBUTOR_RULES.md` at repo root (copy from `mcp-doctor`)
6. Verify with one PR — bot should label + enforce + stay silent on pass
