# Pinch product roadmap proposal

## A. Executive summary

This repository is not evidence of the Pinch product described in the brief. The current codebase is a Python static-analysis/security tool for MCP servers and AI agent tooling, with a strong CLI, policy engine, report generation, live audit flows, and CI automation. The evidence is clear in `README.md`, `pyproject.toml`, `src/vexar_mcp/cli.py`, and `.github/workflows/ci.yml`.

The most important conclusion is not a missing feature list; it is a product mismatch. Pinch is described as a local-first personal assistant for Windows with optional voice, system actions, reminders, clipboard access, and explicit approvals. The repo shows no evidence of a Windows tray/widget shell, no local-data-first runtime, no voice path, no app/file/clipboard automation, and no local model or on-device memory architecture.

Recommended direction:

- Preserve Pinch’s local-first value proposition and explicit approval model.
- Treat the current repository as a useful example of disciplined engineering, strong CI, and honest fail-closed reporting, but not as a starting point for a local assistant product.
- Design a small, high-trust Windows-first product shell before feature breadth. The first product winner is a minimal, continuously available local assistant that performs safe, visible tasks with user approval.
- Keep the added value narrow: local tasks, file handling, clipboard, reminders, basic app/system actions, and optional voice only when clearly consented and user-visible.
- Avoid turning Pinch into a chat website or cloud voice service; do not build competitor “generic AI assistant” features without proving user value in the local context.

Status labeling used below:

- Verified: directly supported by repository evidence.
- Inferred: logical consequence of code/docs evidence.
- Unverified: no direct evidence or unavailable access.

## B. Evidence-backed current-state matrix

| Area | Evidence | Status | Assessment |
|---|---|---|---|
| Repository identity | `README.md`, `pyproject.toml`, `src/vexar_mcp/cli.py` | Verified | This is a security/static-analysis tool for MCP servers, not a Windows personal assistant. The project is `Vexar-MCP` and its description centers on static security and token-cost analysis. |
| CLI and product surface | `src/vexar_mcp/cli.py` | Verified | Mature CLI with `scan`, `audit-server`, `simulate`, `baseline`, `init`, and policy/reporting subcommands. Strong toolchain, but not a local assistant UI or Windows companion. |
| Packaging and installation | `pyproject.toml` | Verified | Python package; install via `pip`, optional extras for YAML/tokenizer/tree-sitter. No Windows installer, tray app, or desktop packaging path is present. |
| CI and release discipline | `.github/workflows/ci.yml`, `.github/roadmap.json` | Verified | Strong engineering gate: lint, type-check, release verification, docs checks, and policy gates. This is a strong platform foundation for a trustworthy product, but it is not evidence of desktop or voice runtime. |
| Web UI / companion app | `README.md`, repository tree | Verified | There is no browser app or Windows companion code in the repo. No tray/app-shell implementation is present. |
| Local model default / local data | `pyproject.toml`, `README.md` | Verified | The repo explicitly favors a dependency-light, mostly-stdlib runtime and optional behavior; that is compatible with local-first defaults, but no model runtime or local agent memory is implemented. |
| Voice / microphone / wake-word | search for `windows|tray|voice|microphone|wake` in repo | Verified: no matches | No voice or microphone code is present. This is a major gap relative to the Pinch brief. |
| Sensitive-action approval | architecture/docs and policy language | Inferred | The repo strongly emphasizes policy gating and fail-closed checks, which aligns with explicit approval requirements; however, there is no user-facing action approval flow for system tasks. |
| Memory / retrieval / preferences | `src/vexar_mcp/config.py`, `policies.py`, `baseline.py` | Inferred | Structured config and baseline records exist for scans and policies, but not for user memory, preference persistence, or portable local profile storage. |
| App/file/system task automation | repository tree | Verified: absent | No app/file automation layer, permission boundaries, or OS integration layer is present. |
| Accessibility for non-technical users | README and docs | Unverified | The project is developer-focused and strongly technical; no evidence of non-technical consumer UX or Windows accessibility work is present. |
| Test quality and release readiness | `tests/` directory, CI workflow | Verified | Test coverage is substantial and CI gate is mature for a static-analysis tool. This demonstrates engineering rigor but does not validate hardware, desktop UX, or actual local assistant tasks. |

Notes:

- Search performed for `windows|tray|voice|microphone|wake|local model|offline|mcp server|audit-server` returned no relevant results for Pinch-specific desktop or assistant functionality.
- This is not an argument that the repo is bad; it is a signal that the product strategy must be intentionally re-scoped from static analysis to local-first assistant runtime.

## C. Existing roadmap reconciliation table

The repository’s active roadmap is curated in `.github/roadmap.json` and reflected in issues such as RM-023, RM-032, RM-033, RM-034, and RM-035. The status below is based on repository evidence only.

| Existing item | Evidence | Current implementation state | Recommendation |
|---|---|---|---|
| RM-023: `chore(release): prove PyPI trusted publishing end to end` | issue #80, `publish-pypi.yml`, `scripts/verify_release.py`, `tests/test_verify_release.py` | Implemented in main partially; remaining trust-publishing validation still open as a roadmap item. | Validate further; keep narrow release-validation work separate from product UI features. |
| RM-032: `feat(live): audit remote servers over Streamable HTTP` | issue #66 | Not implemented in main; open roadmap item. | Next, but not product-critical for Pinch product direction unless a local agent needs network inspection. |
| RM-033: `fix(engine): refuse to read files whose realpath leaves the scan root` | issue #68 | Not implemented in main; open roadmap item. | Validate / keep as security-hardening work. |
| RM-034: `docs(spec): keep a per-revision compatibility matrix current` | issue #71 | Not implemented in main; open roadmap item. | Useful, but not directly relevant to Pinch product direction. |
| RM-035: `chore(ci): unify GitHub Action pins so one major runs per action` | issue #73 | Not implemented in main; open roadmap item. | Keep as maintenance; important for release safety. |
| `roadmap-status` issue | issue #64 | Current status issue exists and tracks roadmap drift. | Good governance pattern; should be mirrored for Pinch roadmap reviews. |

Key reconciliation findings:

- The repo does have a disciplined roadmap and issue process; that should be emulated for Pinch.
- “Merged = finished” is not the default in this repo; the roadmap status issue explicitly cautions against assuming implementation completion from status alone.
- However, none of the current roadmap items relate to Windows desktop UX, voice, or personal assistant workflows. That is a product gap, not a roadmap bug.

## D. Prioritized NOW / NEXT / LATER roadmap

### NOW: next small, achievable delivery cycle

#### 1) Define the Pinch product contract and local-first trust boundaries
- User problem: Without a small, explicit contract, the product drifts into a generic assistant or chat wrapper.
- Supporting evidence: The repo demonstrates strong fail-closed policy thinking (`README.md`, `src/vexar_mcp/policies.py`) and no desktop assistant runtime exists. This is a better foundation for a trust model than for a broad assistant UI.
- Current implementation state: Not implemented. No product shell or service contract exists in this repo.
- Scope:
  - Define the local-first architecture (local model default, local data default, user-owned preferences, explicit approvals).
  - Define the first trusted actions allowed (clipboard read/write, reminders, file open, app launch, short text actions).
  - Define prohibited behaviors (silent voice capture, hidden cloud calls, default mic activation, secret exfiltration).
- Non-goals:
  - No generic chat features, no broad plugin store, no social features, no cross-device sync by default.
- Priority: P0
- Dependencies: design review, risk assessment, privacy review.
- Acceptance criteria:
  - User sees an explicit “what I’m about to do” approval before sensitive actions.
  - Default model/data path stays local and user-visible.
  - No hidden cloud speech path is active without opt-in.
- Automated tests:
  - Unit tests for approval policy and default-local enforcement.
  - Integration tests for privilege boundaries.
- Manual/hardware validation:
  - Windows UX walkthrough with permission prompts and startup/recovery.
- Security/privacy implications:
  - Explicit boundaries around app access, clipboard contents, filesystem, and secrets.
- Effort: 2-4 weeks; confidence: high.
- Success measure: Users can explain what Pinch does, where data lives, and which actions require approval.

#### 2) Ship a minimal Windows companion shell with startup/recovery and consent UI
- User problem: A personal assistant is only useful if it keeps working in the background without surprising the user.
- Supporting evidence: No Windows companion, tray app, or service lifecycle code exists in the repo. This is a direct gap compared with the requested product brief.
- Current implementation state: Not implemented.
- Scope:
  - Windows tray icon or taskbar presence.
  - Startup and recovery policy.
  - Simple status surface: “listening / idle / busy / blocked” with explicit user visibility.
  - Microphone state and permissions visible and controllable.
- Non-goals:
  - No full screen AI dashboard, no full voice assistant shell, no broad automation framework.
- Priority: P0
- Dependencies: Windows packaging and permission review.
- Acceptance criteria:
  - App starts with Windows and shows a visible status state.
  - Recovery after crash or restart returns to a known safe state.
  - Mic activation requires user action and is visibly indicated.
- Automated tests:
  - Service lifecycle and permission-state tests.
- Manual/hardware validation:
  - Real Windows startup/restart and tray interactions.
- Security/privacy implications:
  - Mic and app permissions must be user-controlled; no hidden background capture.
- Effort: 3-6 weeks; confidence: medium-high.
- Success measure: Users can trust the app to remain available and to show its current state without ambiguity.

#### 3) Build the first safe task loop: clipboard + files + reminders + approvals
- User problem: A local assistant is useful only when it can complete a few high-value daily tasks reliably and transparently.
- Supporting evidence: The repo is strong on static analysis and policy gates but lacks any task-execution engine or permission boundary model.
- Current implementation state: Not implemented.
- Scope:
  - Clipboard read/write with explicit consent.
  - Reminder scheduling and tracking.
  - Local file open/save with safe defaults.
  - A single “approve / deny / edit” action gate for sensitive actions.
- Non-goals:
  - No chat-based control plane as the primary workflow.
  - No implicit cross-app automation.
- Priority: P1
- Dependencies: local action sandbox/signals, permission model, UX copy.
- Acceptance criteria:
  - User sees exactly what action is requested before execution.
  - Tasks complete in a visible, reversible way.
  - Failed or denied actions produce clear reasons.
- Automated tests:
  - Task policy tests and action logs.
- Manual/hardware validation:
  - End-to-end daily-workflow tests on Windows.
- Security/privacy implications:
  - Clipboard and file actions are high-risk and must be explicitly approved and logged.
- Effort: 3-5 weeks; confidence: medium.
- Success measure: Users complete one common local action with no ambiguity and without hidden data movement.

### NEXT: work after NOW succeeds

#### 4) Local voice only when explicitly enabled and user-visible
- User problem: Hands-free interaction is valuable only when it is visible, controllable, and privacy-preserving.
- Supporting evidence: No existing voice or wake-word path was found in the repo. This is a non-starter without a deliberate design.
- Scope: optional mic usage, user-visible listening state, clear wake activation, interrupt handling, local speech-to-text if chosen.
- Non-goals: cloud audio required; always-on microphone; hidden wake-word capture.
- Priority: P1
- Dependencies: shell and permission model from NOW.

#### 5) Lightweight local memory and retrieval layer
- User problem: A personal assistant needs memory for preferences and recurring tasks without becoming a surveillance system.
- Scope: user preferences, saved notes, task history, local retrieval, export/import of data.
- Non-goals: cross-device sync by default; opaque telemetry; unbounded long-term memory.
- Priority: P1
- Dependencies: trust model and local data store design.

#### 6) Safe app and system integrations with explicit boundaries
- User problem: System tasks are useful only if the assistant does not overstep into admin/root actions or unapproved behavior.
- Scope: allowed app launches, safe file and clipboard actions, permission categories, action audit log.
- Non-goals: generic remote control of the OS; hidden automation.
- Priority: P1

### LATER: valuable work requiring more evidence

#### 7) On-device model selection and performance tuning
- User problem: Model choice affects quality, latency, and hardware fit.
- Scope: local model selection, quantized options, context window balancing, fallback behavior, performance telemetry with privacy-safe metrics.
- Non-goals: shipping a cloud-only default or benchmarking against competitors without evidence.
- Priority: P2

#### 8) Retrieval and memory portability
- User problem: Users need to move or back up their assistant data without vendor lock-in.
- Scope: export/import, portable profiles, local-only storage format.
- Non-goals: cloud sync as default.

#### 9) Remote service integration only behind explicit opt-in
- User problem: Some workflows are better served via cloud APIs, but defaults must remain local-first.
- Scope: explicit opt-in service integration, graceful failure behavior, permission boundaries.
- Non-goals: mandatory cloud features.

### DEFER / DO NOT BUILD

- Ordinary web chat app as the primary product.
- Always-on cloud audio or hidden recording.
- “Competitor feature parity” without demonstrated user value.
- Broad “agentic” task automation with no approval boundaries.
- Generic plugin marketplaces or unvetted extension execution.

## E. Top five risks and release/validation gates

1. Hidden mic or cloud dependency
   - Risk: The product silently violates local-first and explicit consent.
   - Gate: Mic permission must be visible and user-initiated on every activation path.

2. Over-broad file/app/system access
   - Risk: The assistant gains access to sensitive files or apps without a narrow trust boundary.
   - Gate: Every action must have an approval record with scope, target, and result.

3. Unreliable startup/recovery on Windows
   - Risk: The app becomes a nuisance or fails silently.
   - Gate: Recovery tests after restart, crash, and wake/suspend scenarios.

4. Data leakage or weak local storage
   - Risk: User data or secrets are retained in insecure ways.
   - Gate: Secrets are excluded by default; local storage is encrypted or protected, and export/import is explicit.

5. “Merged = finished” culture replacing product proof
   - Risk: Features look implemented because code is merged, but no user workflow is validated.
   - Gate: Every roadmap item requires user-facing acceptance criteria, manual validation, and a clear “not shipped until” check.

## F. The first three actionable GitHub issue drafts

Issue 1: `feat(app): define Pinch local-first trust contract and action boundaries`

- Summary: Define the product contract for local-first defaults, local data storage, mic permissions, and explicit approvals.
- Problem: Product direction is currently too vague and risks drifting into a generic AI website or cloud product.
- Acceptance criteria:
  - A document defines default-local behavior for model choice, storage, and network access.
  - Every sensitive action path requires explicit approval with visible details.
  - The app explicitly forbids hidden mic capture and cloud audio without opt-in.

Issue 2: `feat(windows): build a minimal tray/status shell with startup and recovery guarantees`

- Summary: Create the first Windows companion shell that survives basic startup/recovery scenarios and shows state clearly.
- Problem: Without a reliable shell, the assistant cannot be continuously available on Windows in a trustworthy way.
- Acceptance criteria:
  - App starts with Windows and shows status.
  - App recovers gracefully after restart without hidden state.
  - Mic state is visible and user-controlled.

Issue 3: `feat(tasks): safe clipboard/files/reminders workflow with explicit approval gate`

- Summary: Support a narrow, high-value workflow for clipboard, file, and reminder actions behind approval.
- Problem: The local assistant is only useful if it can do a few daily tasks without violating trust.
- Acceptance criteria:
  - Users must approve any sensitive action before it executes.
  - The system logs the action and result.
  - The workflow works end-to-end for a common local scenario.

## G. Unknowns and questions requiring maintainer decisions

- Is Pinch intended to be Windows-only or cross-platform with Windows as the first class target?
- What is the intended local model stack: CPU-only local inference, a single embedded model, or a model chooser with constrained hardware profiles?
- Is microphone usage optional and off by default for all sessions, or only for some workflows?
- What counts as a “sensitive action” for the product: clipboard, files, apps, browser tabs, system settings, credentials, or all of the above?
- What are the user privacy expectations for reminders, notes, and task history: local-only, encrypted, exportable, or cloud-synced when opted in?
- What is the minimum evidence required to label a feature “released” vs. “prototype” when the repo lacks hardware testing?
- Does the product handle accessibility needs from day one, or is it a later milestone?

## H. Checks run, exact results and checks not performed

This session did not provide a shell execution environment for the repository, so no repository-level commands were run here. That is a limitation, not a claim of success.

Exact commands to validate this product direction in a real environment:

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy src
pytest --cov=vexar_mcp --cov-report=term-missing --cov-fail-under=85
python .github/scripts/validate_workflows.py
python .github/scripts/sync_roadmap_issues.py --dry-run
```

Recommended additional product-specific validation for Pinch (not run here):

```bash
# Windows startup and tray lifecycle
# Windows UX manual smoke tests for startup, mic permission, and task approval
# Local-only storage validation with a disposable profile
# Explicit approval flow validation for clipboard/file/app actions
# Optional voice workflow validation only with user consent and visible state
```

Checks not performed in this session:

- No real Windows runtime validation.
- No mic capture or hardware validation.
- No local model performance checks.
- No product usability testing.
- No release or packaging validation on hardware.

## Final note

This repo is a strong engineering artefact for a static-analysis and policy-gate product, but it is not evidence that the Pinch brief is already implemented. The correct strategic move is to keep the engineering discipline, but deliberately pivot the roadmap toward a small, visible, local-first Windows assistant with strict approval and local defaults. That preserves trust, avoids feature sprawl, and aligns with the product brief.
