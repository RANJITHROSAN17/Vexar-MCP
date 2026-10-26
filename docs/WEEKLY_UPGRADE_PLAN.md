# mcp-doctor Weekly Upgrade Plan — 90-Day Roadmap (Automated)

Source: audit comparison against leading MCP/security repos + user request.
Method: Bot-driven (Feature Upgrade Bot + Issue Curator Bot + Code Review Bot).

---

## Week 1 (Phase 1 — Foundation)
- [ ] Confirm `MCP009` protocol rule passes CI (already scaffolded; verify with contributor)
- [ ] Merge PR #19 (contributor fix: loader + test snippet)
- [ ] Enable GitHub Pages (`Settings > Pages > GitHub Actions`)
- [ ] Link PyPI Trusted Publisher (`Settings > PyPI > Pending Publishers`)

Bot automation: `feature-upgrade-bot.yml` probes queued roadmap daily; `roadmap-issue-bot.yml` opens `RM-009` contributor issue if not open.

---

## Week 2 (Phase 1 — Risk Engine)
- [ ] Add severity model (Critical / High / Medium / Low) to all findings
- [ ] Update `engine.py` to output `severity` + `exploitability` + `mitigation`
- [ ] Update `MCP001`–`MCP009` rules with metadata (confidence, fix guidance, references)
- [ ] Write `tests/test_risk_score.py`

Bot automation: `issue-raise-bot.yml` opens new contributor issue `RM-016` (good first issue / risk engine) when queued.

---

## Week 3 (Phase 1 — Policy As-Code)
- [ ] Write `docs/POLICY_ENGINE.md` (already done) → turn into `.github/policies/` JSON templates
- [ ] Create `strict-prod`, `startup-safe`, `enterprise-gov` policy files
- [ ] Update CI gate to enforce policy-file presence on PRs adding new rules

Bot automation: `pr-governance.yml` applies policy checks; `welcome-bot.yml` greets new contributors to policy pack PRs.

---

## Week 4 (Phase 1 — Baseline / Diff)
- [ ] Add `mcp-doctor --baseline` CLI command (compare current vs previous scan)
- [ ] Add `tests/test_baseline.py`
- [ ] Update `assets/dashboard.html` to show diff metrics (new / resolved / recurring)

Bot automation: `issue-resolve-bot.yml` promotes next queued issue when baseline PR closes.

---

## Week 5 (Phase 2 — MCP Protocol / Transport)
- [ ] Expand `MCP009` to cover stdio / SSE / Streamable HTTP / WebSocket / auth validation
- [ ] Update `loaders/` with transport-layer checks
- [ ] Add `tests/test_protocol_009.py`

Bot automation: `feature-upgrade-bot.yml` proposes `RM-017` (protocol validation) from roadmap when previous closes.

---

## Week 6 (Phase 2 — Tool Simulation)
- [ ] Build `simulate/` framework for malicious tool responses (shell exposure, env leakage, file write)
- [ ] Add adversarial scenario tests (`tests/test_simulate.py`)
- [ ] Integrate with `cli_simulate.py`

Bot automation: `roadmap-issue-bot.yml` replenishes `RM-018` (simulation) automatically.

---

## Week 7 (Phase 2 — Token Budget Enhancement)
- [ ] Enhance `simulate/cost_model.py` with per-tool chains, recursion detection, large-context blowup
- [ ] Add `--model` CLI option with real LLM pricing references
- [ ] Update dashboard to show cost projections

Bot automation: `roadmap-issue-bot.yml` proposes `RM-019` if not open.

---

## Week 6 (Phase 3 — Dashboard / Reporting — actually overlaps with Phase 2, but continuous)
- [ ] Deploy `assets/dashboard.html` to GitHub Pages (enable Settings > Pages)
- [ ] Add `audit-report.json` generation to CI
- [ ] Make dashboard fetch live data from CI artifacts

Bot automation: `release.yml` can include dashboard asset in releases.

---

## Week 8 (Phase 3 — Enterprise Policy / Reporting)
- [ ] Finalize `POLICY_ENGINE.md` with full JSON policy schema
- [ ] Add `baseline-vs-gate` mode to CI (fail on new critical findings only)
- [ ] Document enterprise reporting format (JSON / SARIF / HTML)

Bot automation: `stale-bot.yml` keeps policy docs fresh; `pr-governance.yml` ensures new rules include benchmark tables.

---

## Weekly automation (already running — no manual steps needed)

- Monday 09:00 UTC: Feature Upgrade Bot proposes next roadmap item
- Monday 10:00 UTC: Issue Raise Bot opens contributor issue
- Daily: Code Review Bot verifies every PR live
- Every PR merge: Issue Resolve Bot closes linked issue + promotes next
- Every PR attempt: Final Merge Bot pings you (only when YOU approve)
- Sunday 00:00 UTC: Stale Bot gently reminds on old issues
- Weekly: Dependabot groups updates (max 2 PRs)

---

## Success criteria for the 90-day plan

- [ ] `MCP009` passes CI and is documented
- [ ] Risk engine scores findings with severity
- [ ] Baseline diff produces visible output
- [ ] Policy files exist and are enforced by CI
- [ ] Dashboard is live at `ranjithrosan17.github.io/mcp-doctor/`
- [ ] 8 open contributor issues remain active; 15+ queued; new ones open automatically
- [ ] PR template requires quantifiable impact (already done)
- [ ] Zero manual bot configuration needed (all live, only 3 notifications to you)
