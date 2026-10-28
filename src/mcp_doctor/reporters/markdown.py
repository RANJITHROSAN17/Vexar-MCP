"""GitHub Pull Request / Step Summary Markdown reporter."""

from __future__ import annotations

from mcp_doctor.baseline import BaselineDelta
from mcp_doctor.models import ScanReport, Severity
from mcp_doctor.risk import risk_band

_SEV_ICON = {
    Severity.ERROR: "🔴 `error`",
    Severity.WARNING: "🟡 `warning`",
    Severity.INFO: "🔵 `info`",
}
_MAX_LISTED = 25


def render_markdown(report: ScanReport, delta: BaselineDelta | None = None) -> str:
    """Format a ScanReport as GitHub-Flavored Markdown.

    Args:
        report: The scan to render.
        delta: Optional baseline comparison; when supplied, the summary leads with the
            change in risk rather than the absolute backlog, which is what a reviewer
            on a pull request actually needs to act on.
    """
    s = report.summary
    lines: list[str] = [
        "## Vexar-MCP Audit Summary",
        "",
        "| Health Score | Grade | Risk | Tools Audited | Est. Context Tax | Errors | Warnings | Info |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        (
            f"| **{s.score}/100** | **{s.grade}** | {s.risk_index} ({risk_band(s.risk_index)}) "
            f"| {s.tools_scanned} | ~{s.total_estimated_tokens} tokens "
            f"| {s.errors} | {s.warnings} | {s.infos} |"
        ),
        "",
        f"_Policy: `{report.policy_name}`_",
        "",
    ]

    if delta is not None:
        lines.extend(
            [
                "### Baseline delta",
                "",
                f"- 🆕 **{len(delta.new_findings)}** new finding(s)",
                f"- ✅ **{len(delta.resolved)}** resolved since baseline",
                f"- ➖ {delta.unchanged} pre-existing finding(s) accepted by the baseline",
                "",
            ]
        )

    findings = (
        delta.new_findings if delta is not None and delta.new_findings else report.diagnostics
    )
    heading = "New findings" if delta is not None and delta.new_findings else "Findings"

    if not findings:
        tail = (
            "✅ **Zero new security, schema, contract, or token-budget issues introduced.**"
            if delta is not None
            else "✅ **Zero security, schema, contract, or token-budget issues detected.**"
        )
        lines.append(tail)
        return "\n".join(lines)

    lines.extend(
        [
            f"### {heading}",
            "",
            "| Severity | Rule | Tool | Location | Message | Risk |",
            "| :--- | :--- | :--- | :--- | :--- | :---: |",
        ]
    )
    for d in findings[:_MAX_LISTED]:
        msg = d.message.replace("|", "\\|")
        lines.append(
            f"| {_SEV_ICON[d.severity]} | `{d.rule_id}` | `{d.tool_name}` "
            f"| `{d.source_file}:{d.line}` | {msg} | {d.risk} |"
        )
    if len(findings) > _MAX_LISTED:
        lines.append("")
        lines.append(
            f"_{len(findings) - _MAX_LISTED} further finding(s) omitted; see the JSON or HTML report._"
        )
    return "\n".join(lines)
