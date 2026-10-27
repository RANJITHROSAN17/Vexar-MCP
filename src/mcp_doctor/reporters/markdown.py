"""GitHub Pull Request / Step Summary Markdown reporter."""

from __future__ import annotations

from mcp_doctor.models import ScanReport, Severity

_SEV_ICON = {
    Severity.ERROR: "🔴 `error`",
    Severity.WARNING: "🟡 `warning`",
    Severity.INFO: "🔵 `info`",
}


def render_markdown(report: ScanReport) -> str:
    """Format a ScanReport as GitHub-Flavored Markdown."""
    s = report.summary
    lines: list[str] = [
        "## Vexar-MCP Audit Summary",
        "",
        "| Health Score | Grade | Tools Audited | Est. Context Tax | Errors | Warnings | Info |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        (
            f"| **{s.score}/100** | **{s.grade}** | {s.tools_scanned} "
            f"| ~{s.total_estimated_tokens} tokens | {s.errors} | {s.warnings} | {s.infos} |"
        ),
        "",
    ]

    if not report.diagnostics:
        lines.append("✅ **Zero security, schema, contract, or token-budget issues detected.**")
        return "\n".join(lines)

    lines.extend(
        [
            "### Findings",
            "",
            "| Severity | Rule | Tool | Location | Message |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for d in report.diagnostics:
        msg = d.message.replace("|", "\\|")
        lines.append(
            f"| {_SEV_ICON[d.severity]} | `{d.rule_id}` | `{d.tool_name}` "
            f"| `{d.source_file}:{d.line}` | {msg} |"
        )
    return "\n".join(lines)
