"""GitHub Pull Request / Step Summary Markdown reporter."""

from __future__ import annotations

from mcp_doctor.baseline import BaselineDelta
from mcp_doctor.models import ScanReport, ScanStatus, Severity
from mcp_doctor.risk import risk_band
from mcp_doctor.sanitize import neutralize_markdown

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
    c = report.coverage
    score_cell = "n/a" if s.score is None else f"{s.score}/100"
    status_icon = {
        ScanStatus.CLEAN: "✅",
        ScanStatus.FINDINGS: "🔎",
        ScanStatus.PARTIAL: "⚠️",
        ScanStatus.NO_MATCHES: "❔",
        ScanStatus.ERROR: "⛔",
    }[report.status]
    lines: list[str] = [
        "## Vexar-MCP Audit Summary",
        "",
        (
            f"**Status:** {status_icon} `{report.status.value.upper()}`  |  "
            f"**Policy:** `{report.policy_name}`"
        ),
        "",
        "| Health Score | Grade | Risk | Tools Audited | Est. Context Tax | Errors | Warnings | Info |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        (
            f"| **{score_cell}** | **{s.grade}** | {s.risk_index} ({risk_band(s.risk_index)}) "
            f"| {s.tools_scanned} | ~{s.total_estimated_tokens} tokens "
            f"| {s.errors} | {s.warnings} | {s.infos} |"
        ),
        "",
        (
            f"_Coverage: {c.candidate_files} candidate file(s) · {c.files_analyzed} analyzed · "
            f"{c.files_failed} failed · {c.files_with_tools} with tools_"
        ),
        "",
    ]
    if report.parse_issues:
        lines.extend(["### Analysis issues — these inputs were **not** checked", ""])
        for issue in report.parse_issues[:_MAX_LISTED]:
            location = f":{issue.line}" if issue.line else ""
            lines.append(
                f"- `{issue.kind.value}` — `{neutralize_markdown(issue.source_file)}{location}`: "
                f"{neutralize_markdown(issue.message)}"
            )
        if len(report.parse_issues) > _MAX_LISTED:
            lines.append(f"- _{len(report.parse_issues) - _MAX_LISTED} further file(s) omitted._")
        lines.append("")
    if report.status is ScanStatus.NO_MATCHES:
        lines.extend(
            [
                "> ⚠️ **No supported MCP tool was recognized.** Nothing was graded, so this is "
                "not a clean result. Check that the target contains MCP tool registrations or "
                "a tool manifest this version can read.",
                "",
            ]
        )

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
        if delta is not None:
            tail = "✅ **Zero new security, schema, contract, or token-budget issues introduced.**"
        elif report.status is ScanStatus.CLEAN:
            tail = "✅ **Zero security, schema, contract, or token-budget issues detected.**"
        else:
            tail = (
                "⚠️ **No findings in the files that were analyzed — but the result is "
                f"`{report.status.value.upper()}`, not clean.** Fix the analysis issues above "
                "first; a passing gate on unreadable code means nothing."
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
        # Untrusted text must not be able to open a code fence, start a heading, or begin a
        # CI workflow command inside a report that gets posted as a PR comment.
        msg = neutralize_markdown(d.message).replace("|", "\\|")
        tool = neutralize_markdown(d.tool_name)
        location = neutralize_markdown(f"{d.source_file}:{d.line}")
        lines.append(
            f"| {_SEV_ICON[d.severity]} | `{d.rule_id}` | `{tool}` "
            f"| `{location}` | {msg} | {d.risk} |"
        )
    if len(findings) > _MAX_LISTED:
        lines.append("")
        lines.append(
            f"_{len(findings) - _MAX_LISTED} further finding(s) omitted; see the JSON or HTML report._"
        )
    return "\n".join(lines)
