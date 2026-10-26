# Contributing to `mcp-doctor`

> **Metrics-First Requirement:** Every PR must include a `Quantifiable Impact` section with Before/After/Improvement percentages (speed, memory, coverage). Qualitative claims alone are not accepted. See `.github/PULL_REQUEST_TEMPLATE.md`.

Thank you for your interest in contributing to **`mcp-doctor`**! Every lint rule, loader, and output reporter is intentionally modular so you can ship an impactful Pull Request in a single sitting.

---

## Finding Something to Work On

Check our [**`good first issue`**](https://github.com/RANJITHROSAN17/mcp-doctor/labels/good%20first%20issue) and [**`help wanted`**](https://github.com/RANJITHROSAN17/mcp-doctor/labels/help%20wanted) queues! Our automated **Roadmap & Issue Curator Bot** keeps contributor-ready tasks stocked with exact file paths, acceptance criteria, and verification commands.

1. Comment on the issue you would like to tackle.
2. Fork the repository and create a feature branch (`git checkout -b feat/mcp009-secret-detector`).
3. Open a Pull Request referencing `Closes #<issue-number>`!

---

## Local Development Setup

```bash
git clone https://github.com/<your-username>/mcp-doctor.git
cd mcp-doctor
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
