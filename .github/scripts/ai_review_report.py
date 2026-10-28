#!/usr/bin/env python3
"""Generate the AI-review bot's audit report.

This used to be an inline ``python -c "..."`` block inside the workflow. Nested quoting
inside a YAML block scalar inside a shell double-quoted string is fragile — it eventually
produced ``SyntaxError: unterminated string literal`` and the bot failed on every run. A
real file is testable, diffable and reviewable.

Writes ``ai_review_report.md`` and exits 0 even when there is nothing to report: a bot
that fails because the codebase is clean is a broken bot.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp_doctor.engine import scan_paths
from mcp_doctor.models import Diagnostic

MAX_DIAGNOSTICS = 200
REMEDIATION_CHARS = 60


def _severity_name(diagnostic: Diagnostic) -> str:
    return diagnostic.severity.name


def build_report(target: Path) -> str:
    """Return the Markdown report for a scan of ``target``."""
    results = scan_paths([target])
    summary = results.summary

    lines: list[str] = [
        "🤖 **AI Code Reviewer + Enhancer (Vexar-MCP)**",
        "",
        f"**Score:** {summary.score}/100  |  **Grade:** {summary.grade}  |  "
        f"**Risk index:** {summary.risk_index}",
        f"**Errors:** {summary.errors}  |  **Warnings:** {summary.warnings}  |  "
        f"**Info:** {summary.infos}",
        f"**Tools audited:** {summary.tools_scanned} across {summary.files_scanned} file(s)  |  "
        f"**Est. context cost:** ~{summary.total_estimated_tokens} tokens/turn",
        "",
    ]

    if results.diagnostics:
        lines.extend(
            [
                "| Rule | Severity | Location | Tool | Remediation |",
                "|---|---|---|---|---|",
            ]
        )
        for diagnostic in results.diagnostics[:MAX_DIAGNOSTICS]:
            source = diagnostic.source_file.split("/")[-1] if diagnostic.source_file else "—"
            remediation = diagnostic.remediation[:REMEDIATION_CHARS].replace("|", "\\|")
            lines.append(
                f"| {diagnostic.rule_id} | {_severity_name(diagnostic)} "
                f"| {source}:{diagnostic.line} | {diagnostic.tool_name} | {remediation} |"
            )
        if len(results.diagnostics) > MAX_DIAGNOSTICS:
            lines.append("")
            lines.append(
                f"_{len(results.diagnostics) - MAX_DIAGNOSTICS} further finding(s) omitted; "
                "see the SARIF or HTML report._"
            )
    else:
        lines.append("✅ No security, schema, contract, or token-budget findings.")

    security = [d for d in results.diagnostics if d.category.name == "SECURITY"]
    schema = [d for d in results.diagnostics if d.category.name == "SCHEMA"]
    tokens = [d for d in results.diagnostics if d.category.name == "TOKENS"]

    lines.extend(["", "---", "### Enhancements"])
    if security:
        lines.append(
            f"- **Security**: address {len(security)} sink finding(s); prioritise "
            "`MCP005` (shell), `MCP013` (deserialisation) and `MCP009` (secrets)."
        )
    if schema:
        lines.append(
            f"- **Schema**: {len(schema)} finding(s); add bounds (`maximum`, `maxItems`, "
            "`le=`) and explicit types so the model can fill arguments safely."
        )
    if tokens:
        lines.append(
            f"- **Token footprint**: {len(tokens)} tool(s) over budget; trim parameter "
            "descriptions before splitting tools."
        )
    if not (security or schema or tokens):
        lines.append("- Nothing outstanding. Keep the gate on `strict-prod` in CI.")

    lines.extend(
        [
            "",
            "### Next steps",
            "- Gate merges with `vexar-mcp scan . --policy strict-prod`.",
            "- Adopt incrementally with `--baseline .vexar-baseline.json` if the backlog is large.",
            "- Emit SARIF (`--format sarif`) for GitHub Code Scanning.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    target = Path(argv[0]) if argv else Path(".")
    output = Path("ai_review_report.md")
    output.write_text(build_report(target), encoding="utf-8")
    print(f"Report written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
