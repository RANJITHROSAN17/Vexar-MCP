"""Human-friendly terminal scorecard and diagnostic formatter."""

from __future__ import annotations

from collections.abc import Sequence

from mcp_doctor.models import ScanReport, Severity
from mcp_doctor.rules.base import RuleDefinition

_SEV_BADGE = {
    Severity.ERROR: "[ERROR]",
    Severity.WARNING: "[WARN ]",
    Severity.INFO: "[INFO ]",
}


def render_terminal(report: ScanReport) -> str:
    """Format a ScanReport for terminal output."""
    lines: list[str] = []
    s = report.summary
    lines.append("=" * 78)
    lines.append(
        f"Vexar-MCP  |  Health Score: {s.score}/100 ({s.grade})  |  "
        f"Tools: {s.tools_scanned} across {s.files_scanned} file(s)"
    )
    lines.append(
        f"Context Footprint: ~{s.total_estimated_tokens} tokens  |  "
        f"Errors: {s.errors}  Warnings: {s.warnings}  Info: {s.infos}"
    )
    lines.append("=" * 78)

    if not report.tools:
        lines.append("No MCP tools or agent tool manifests discovered in target path(s).")
        return "\n".join(lines)

    if not report.diagnostics:
        lines.append(
            "All checks passed! Zero security, schema, contract, or token-budget findings."
        )
        return "\n".join(lines)

    lines.append("")
    for diag in report.diagnostics:
        badge = _SEV_BADGE[diag.severity]
        lines.append(
            f"{badge} {diag.rule_id} ({diag.rule_slug}) at {diag.source_file}:{diag.line} [{diag.tool_name}]"
        )
        lines.append(f"        {diag.message}")
        if diag.remediation:
            lines.append(f"        Fix: {diag.remediation}")
    return "\n".join(lines)


def render_token_report(report: ScanReport) -> str:
    """Render per-tool context-window token usage leaderboard."""
    lines: list[str] = [
        f"{'TOOL NAME':<32} {'EST. TOKENS':>12} {'PARAMS':>8}  SOURCE",
        "-" * 78,
    ]
    for tool in sorted(report.tools, key=lambda t: t.estimated_tokens, reverse=True):
        lines.append(
            f"{tool.name:<32} {tool.estimated_tokens:>12} {len(tool.parameters):>8}  "
            f"{tool.source_file}:{tool.line}"
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
