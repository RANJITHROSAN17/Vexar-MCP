"""Human-friendly terminal scorecard and diagnostic formatter."""

from __future__ import annotations

from collections.abc import Sequence

from mcp_doctor.baseline import BaselineDelta
from mcp_doctor.models import ScanReport, ScanStatus, Severity
from mcp_doctor.risk import risk_band
from mcp_doctor.rules.base import RuleDefinition
from mcp_doctor.sanitize import strip_control_chars

_SEV_BADGE = {
    Severity.ERROR: "[ERROR]",
    Severity.WARNING: "[WARN ]",
    Severity.INFO: "[INFO ]",
}


def _t(text: str) -> str:
    """Strip invisible control characters from text that came from a scanned file.

    A tool name is untrusted input: a scanned repository can put ``\x1b[2K`` in one and erase
    the line above it in a CI log, or an OSC-8 sequence that turns a finding into a link.
    """
    return strip_control_chars(text, keep_newlines=False)


_STATUS_LABEL = {
    ScanStatus.CLEAN: "CLEAN - analyzed everything, no findings",
    ScanStatus.FINDINGS: "FINDINGS - analyzed everything, issues reported",
    ScanStatus.PARTIAL: "PARTIAL - some inputs could not be analyzed (results incomplete)",
    ScanStatus.NO_MATCHES: "NO MATCHES - no supported MCP tool was recognized",
    ScanStatus.ERROR: "ERROR - nothing could be analyzed",
}


def _score_text(report: ScanReport) -> str:
    score = report.summary.score
    return "n/a (nothing to grade)" if score is None else f"{score}/100 ({report.summary.grade})"


def render_terminal(report: ScanReport, delta: BaselineDelta | None = None) -> str:
    """Format a ScanReport for terminal output."""
    lines: list[str] = []
    s = report.summary
    lines.append("=" * 78)
    lines.append(
        f"Vexar-MCP  |  Health Score: {_score_text(report)}  |  "
        f"Tools: {s.tools_scanned} across {s.files_scanned} file(s)"
    )
    lines.append(
        f"Context Footprint: ~{s.total_estimated_tokens} tokens  |  "
        f"Errors: {s.errors}  Warnings: {s.warnings}  Info: {s.infos}"
    )
    c = report.coverage
    # Live audits must say which revision they spoke and how they agreed it: the two
    # negotiation modes prove different things about the server.
    if report.live_protocol:
        lines.append(f"Live protocol: {report.live_protocol}  |  negotiation: {report.live_mode}")
    lines.append(
        f"Policy: {report.policy_name}  |  Risk Index: {s.risk_index}/100 "
        f"({risk_band(s.risk_index)})  |  Status: {report.status.value.upper()}"
    )
    lines.append(
        f"Coverage: {c.candidate_files} candidate file(s), {c.files_analyzed} analyzed, "
        f"{c.files_failed} failed, {c.files_with_tools} with tools"
    )
    lines.append("=" * 78)
    lines.append(_STATUS_LABEL[report.status])

    if report.parse_issues:
        lines.append("")
        lines.append("ANALYSIS ISSUES (these inputs were NOT checked)")
        lines.append("-" * 78)
        for issue in report.parse_issues:
            location = f":{issue.line}" if issue.line else ""
            lines.append(f"[{issue.kind.value}] {_t(issue.source_file)}{location}")
            lines.append(f"        {_t(issue.message)}")

    if not report.tools:
        lines.append("")
        lines.append(
            "No MCP tools or agent tool manifests discovered in target path(s). "
            "Nothing was graded — this is not a clean bill of health."
        )
        return "\n".join(lines)

    if delta is not None:
        lines.append(
            f"Baseline: {len(delta.new_findings)} new, {len(delta.resolved)} resolved, "
            f"{delta.unchanged} unchanged"
        )
        lines.append("")

    if not report.diagnostics:
        if report.status is ScanStatus.CLEAN:
            lines.append(
                "All checks passed! Zero security, schema, contract, or token-budget findings."
            )
        else:
            lines.append(
                "No findings in the files that were analyzed - but the analysis was not "
                "complete, so this is NOT a clean result."
            )
        return "\n".join(lines)

    if delta is not None:
        lines.append("NEW FINDINGS")
        lines.append("-" * 78)
        if not delta.new_findings:
            lines.append("None. No regressions introduced since the baseline.")
            return "\n".join(lines)
        for diag in delta.new_findings:
            lines.append(
                f"{_SEV_BADGE[diag.severity]} {diag.rule_id} ({diag.rule_slug}) at "
                f"{_t(diag.source_file)}:{diag.line} [{_t(diag.tool_name)}]"
            )
            lines.append(f"        {_t(diag.message)}")
            if diag.remediation:
                lines.append(f"        Fix: {_t(diag.remediation)}")
        return "\n".join(lines)

    lines.append("")
    for diag in report.diagnostics:
        badge = _SEV_BADGE[diag.severity]
        lines.append(
            f"{badge} {diag.rule_id} ({diag.rule_slug}) at {_t(diag.source_file)}:{diag.line} "
            f"[{_t(diag.tool_name)}] risk={diag.risk}"
        )
        lines.append(f"        {_t(diag.message)}")
        if diag.remediation:
            lines.append(f"        Fix: {_t(diag.remediation)}")
    return "\n".join(lines)


def render_token_report(report: ScanReport) -> str:
    """Render per-tool context-window token usage leaderboard."""
    lines: list[str] = [
        f"{'TOOL NAME':<32} {'EST. TOKENS':>12} {'PARAMS':>8}  SOURCE",
        "-" * 78,
    ]
    for tool in sorted(report.tools, key=lambda t: t.estimated_tokens, reverse=True):
        lines.append(
            f"{_t(tool.name)[:32]:<32} {tool.estimated_tokens:>12} "
            f"{len(tool.parameters):>8}  {_t(tool.source_file)}:{tool.line}"
        )
    lines.append("-" * 78)
    lines.append(
        f"Total context-window tax across {report.summary.tools_scanned} tool(s): "
        f"~{report.summary.total_estimated_tokens} tokens/turn"
    )
    return "\n".join(lines)


def render_rules_table(rules: Sequence[RuleDefinition]) -> str:
    """Render registered rule catalog."""
    lines: list[str] = [
        f"{'ID':<8} {'SEVERITY':<9} {'CATEGORY':<10} {'SLUG':<34} SUMMARY",
        "-" * 100,
    ]
    for r in rules:
        lines.append(
            f"{r.rule_id:<8} {r.severity.value:<9} {r.category.value:<10} {r.slug:<34} {r.summary}"
        )
    return "\n".join(lines)
