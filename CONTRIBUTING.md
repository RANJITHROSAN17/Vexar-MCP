# Contributing to Vexar-MCP

> **Metrics-First Requirement:** Every PR must include a `Quantifiable Impact` section with Before/After/Improvement percentages (speed, memory, coverage). Qualitative claims alone are not accepted. See `.github/PULL_REQUEST_TEMPLATE.md`.

Thank you for your interest in contributing to **Vexar-MCP**! Every lint rule, loader, and output reporter is intentionally modular so you can ship an impactful Pull Request in a single sitting.

---

## Finding Something to Work On

Check our [**`good first issue`**](https://github.com/RANJITHROSAN17/Vexar-MCP/labels/good%20first%20issue) and [**`help wanted`**](https://github.com/RANJITHROSAN17/Vexar-MCP/labels/help%20wanted) queues! Our automated **Roadmap & Issue Curator Bot** keeps contributor-ready tasks stocked with exact file paths, acceptance criteria, and verification commands.

1. Comment on the issue you would like to tackle.
2. Fork the repository and create a feature branch (`git checkout -b feat/mcp009-secret-detector`).
3. Open a Pull Request referencing `Closes #<issue-number>`!

---

## Local Development Setup

```bash
git clone https://github.com/<your-username>/Vexar-MCP.git
cd Vexar-MCP
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run the full quality gate locally before pushing:

```bash
ruff check .
ruff format --check .
mypy src
pytest --cov=mcp_doctor --cov-report=term-missing
```

---

## How to Add a New Lint Rule in 10 Minutes

1. Pick the matching rule module under `src/mcp_doctor/rules/` (`security_rules.py`, `schema_rules.py`, `token_rules.py`, or `contract_rules.py`).
2. Decorate a check function with `@register_rule(...)`:

```python
@register_rule(
    rule_id="MCP009",
    slug="hardcoded-secret-in-tool",
    severity=Severity.ERROR,
    category=Category.SECURITY,
    summary="Tool handler contains a hardcoded API key or bearer secret.",
    remediation="Load credentials from environment variables or a secret manager.",
)
def check_hardcoded_secrets(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]: ...
```

3. Add positive and negative test cases in `tests/test_rules.py`!
4. Verify `examples/clean_server.py` still scores `100/100 (A+)`.

---

## Pull Request Title Convention

Our **PR Governance Bot** enforces [Conventional Commits](https://www.conventionalcommits.org/):
- `feat(rules): add MCP009 hardcoded secret detector`
- `feat(parsers): support TypeScript MCP SDK`
- `fix(ast): handle async generator tools cleanly`
- `docs: clarify SARIF upload example`

---

## Rule IDs, policies, and rule packs

### Adding a built-in rule

1. Rule IDs come from the `MCP` namespace and are allocated in order: `MCP001`–`MCP011`,
   `MCP013` are static rules, `MCP012` is live-only. `MCP012` is skipped by design in the
   static modules — do not reuse it.
2. Register with `@register_rule(...)` in the appropriate module under
   `src/mcp_doctor/rules/` (`security_rules.py`, `schema_rules.py`, `token_rules.py`,
   `contract_rules.py`).
3. Add the rule to the table in `README.md`, a section in `docs/rules.md` (with a
   remediation example and at least one false-positive case), and tests in
   `tests/test_rules.py`.
4. If the rule can emit an error, consider whether it belongs in a protected category. Add
   it to the relevant presets in `src/mcp_doctor/policies.py` and say so in the PR.

### Changing a policy preset

Presets are a public contract: tightening one changes other people's build outcomes.

- Tightening within a minor release requires a `CHANGELOG.md` "Policy changes" note and a
  rationale in the PR.
- Loosening any preset is reviewed as a **security-relevant** change.
- Adding a preset is additive and needs docs only.

### Adding a rule pack

Organization rules belong in a pack ([docs/rule-packs.md](docs/rule-packs.md)), not in the
built-in catalog. Packs may not use the `MCP` prefix — that namespace is reserved so a pack
can never impersonate a built-in check. Declarative (TOML/JSON) packs are strongly preferred
over Python packs, because they are data and cannot run code.

### Adding a reporter or changing report output

- Report fields are **additive only** inside `vexar-mcp-report/v1`. Never rename or retype an
  existing key; bump the schema instead and document the migration.
- Every reporter must render a report with zero tools, zero findings, and unreasonably long
  messages without breaking.

### Touching the live audit path

`src/mcp_doctor/live.py` runs a child process. Keep these invariants:

- argv lists only, never a shell;
- one total deadline covering handshake and pagination;
- bounded response size and page count;
- terminate the child **before** closing pipes (closing first can deadlock the reader
  threads);
- add a fixture-based test using `tests/fixtures/mock_mcp_server.py` rather than requiring
  a real third-party server.
