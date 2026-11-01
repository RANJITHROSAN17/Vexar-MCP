"""Reporters for a fleet audit: many MCP servers in one document.

A fleet report answers a different question from a single-server report. Per-server
detail exists, but the reader's first question is "which of my servers is the problem
and what is not covered?", so every format leads with a per-server table and a coverage
line before it shows a single finding.

Three properties are deliberate in all three formats:

* the **fleet score is the worst server**, and it says so in words next to the number,
  because a reader who assumes it is an average will mis-read it;
* servers that failed or were skipped are shown **in the table**, not omitted — a fleet
  report that lists only what it managed to audit looks more complete than it is;
* a fleet with any failure is marked **incomplete** wherever its status is shown.

Everything a registry file can influence — server names, error strings, tool names — is
untrusted text, and is passed through the same control-character sanitiser the terminal
and HTML single-server reporters use. A registry is user-supplied data that names
programs; it should not be able to write escape sequences into a CI log either.
"""

from __future__ import annotations

import html
import json

from mcp_doctor import __version__
from mcp_doctor.models import ScanStatus, Severity
from mcp_doctor.registry import (
    OUTCOME_AUDITED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    FleetReport,
    ServerAudit,
)
from mcp_doctor.reporters.json_reporter import report_payload
from mcp_doctor.sanitize import strip_control_chars

_STATUS_COLOR = {
    ScanStatus.CLEAN: "#16a34a",
    ScanStatus.FINDINGS: "#d97706",
    ScanStatus.PARTIAL: "#dc2626",
    ScanStatus.NO_MATCHES: "#0284c7",
    ScanStatus.ERROR: "#dc2626",
}
_OUTCOME_MARK = {OUTCOME_AUDITED: "ok  ", OUTCOME_FAILED: "FAIL", OUTCOME_SKIPPED: "skip"}

_FLEET_SCORE_NOTE = (
    "the worst audited server, not the average - an average lets one vulnerable "
    "server hide behind healthy ones"
)


def _t(text: str) -> str:
    """Strip invisible control characters from untrusted registry text."""
    return strip_control_chars(text, keep_newlines=False)


def _score_text(audit: ServerAudit) -> str:
    if audit.report is None:
        return "-"
    score = audit.report.summary.score
    return "n/a" if score is None else f"{score}/100 {audit.report.summary.grade}"


def _fleet_score_text(fleet: FleetReport) -> str:
    score = fleet.score
    return "n/a (nothing graded)" if score is None else f"{score}/100 ({fleet.grade})"


def _coverage_text(fleet: FleetReport) -> str:
    total = len(fleet.audits)
    audited = len(fleet.audited)
    parts = [f"{audited} of {total} server(s) audited"]
    if fleet.failed:
        parts.append(f"{len(fleet.failed)} could not be audited")
    if fleet.skipped:
        parts.append(f"{len(fleet.skipped)} skipped (unsupported transport)")
    return ", ".join(parts)


def render_fleet_terminal(fleet: FleetReport) -> str:
    """Format a FleetReport for a terminal.

    Args:
        fleet: The combined report.

    Returns:
        A multi-line string: fleet scorecard, one line per server, then any diagnostics
        from servers that were audited.
    """
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(
        f"Vexar-MCP fleet  |  {_t(fleet.registry_path)}  |  format: {_t(fleet.source_format)}"
    )
    lines.append(
        f"Fleet score: {_fleet_score_text(fleet)}  |  "
        f"risk: {fleet.risk_index}  |  tools: {fleet.tools}"
    )
    lines.append(f"Coverage: {_coverage_text(fleet)}")
    if fleet.score is not None:
        worst = fleet.worst_server
        worst_name = _t(worst.name) if worst is not None else "-"
        lines.append(f"Worst server: '{worst_name}' - {_FLEET_SCORE_NOTE}")
        lines.append(f"  mean across audited servers: {fleet.mean_score}/100 (context only)")
    errors, warnings, infos = fleet.counts
    lines.append(
        f"Totals: {errors} error(s), {warnings} warning(s), {infos} info  |  "
        f"policy '{_t(fleet.policy_name)}'"
    )
    lines.append(f"Status: {fleet.status.value.upper()}")
    if not fleet.complete:
        lines.append(
            "  ! This fleet is INCOMPLETE: findings below cover only the servers that were audited."
        )
    lines.append("")

    width = max([len(_t(a.name)) for a in fleet.audits] + [10])
    lines.append(f"  {'SERVER'.ljust(width)}  {'RESULT':<6} {'SCORE':<12} {'TOOLS':>5}  DETAIL")
    lines.append(f"  {'-' * width}  {'-' * 6} {'-' * 12} {'-' * 5}  {'-' * 24}")
    for audit in fleet.audits:
        name = _t(audit.name).ljust(width)
        mark = _OUTCOME_MARK.get(audit.outcome, audit.outcome)
        score = _score_text(audit).ljust(12)
        tools = "-" if audit.report is None else str(audit.report.summary.tools_scanned)
        if audit.report is not None:
            detail = f"{audit.report.status.value}, {audit.duration_ms}ms"
        else:
            detail = _t(audit.error or "")
        lines.append(f"  {name}  {mark:<6} {score} {tools:>5}  {detail}")

    for audit in fleet.audited:
        if audit.report is None or not audit.report.diagnostics:
            continue
        lines.append("")
        lines.append(f"--- {_t(audit.name)} — {_score_text(audit)} " + "-" * 20)
        for diagnostic in audit.report.diagnostics:
            badge = {
                Severity.ERROR: "[ERROR]",
                Severity.WARNING: "[WARN ]",
                Severity.INFO: "[INFO ]",
            }[diagnostic.severity]
            lines.append(
                f"  {badge} {diagnostic.rule_id} {_t(diagnostic.tool_name)}: "
                f"{_t(diagnostic.message)}"
            )
    lines.append("=" * 78)
    return "\n".join(lines)


def render_fleet_json(fleet: FleetReport) -> str:
    """Serialize a FleetReport as JSON, embedding each server's own report payload.

    Args:
        fleet: The combined report.

    Returns:
        Pretty-printed JSON. ``schema`` is ``vexar-fleet/v1`` and, like the single-server
        document, is additive-only across minor releases.
    """
    payload: dict[str, object] = {
        "schema": "vexar-fleet/v1",
        "version": __version__,
        "registry": {
            "path": fleet.registry_path,
            "format": fleet.source_format,
            "servers": len(fleet.audits),
        },
        "policy": {"name": fleet.policy_name},
        "status": fleet.status.value,
        # Coverage is stated separately from findings on purpose: an empty diagnostics
        # list next to "4 of 6 audited" means nothing, and consumers must not read it as clean.
        "complete": fleet.complete,
        "coverage": {
            "servers": len(fleet.audits),
            "audited": len(fleet.audited),
            "failed": len(fleet.failed),
            "skipped": len(fleet.skipped),
        },
        "fleet": {
            # `score` is null when nothing was graded, and is the minimum when it is not.
            "score": fleet.score,
            "grade": fleet.grade,
            "score_semantics": "minimum across audited servers",
            "mean_score": fleet.mean_score,
            "worst_server": None if fleet.worst_server is None else fleet.worst_server.name,
            "risk_index": fleet.risk_index,
            "tools": fleet.tools,
            "errors": fleet.counts[0],
            "warnings": fleet.counts[1],
            "infos": fleet.counts[2],
        },
        "servers": [_server_payload(audit) for audit in fleet.audits],
    }
    return json.dumps(payload, indent=2)


def _server_payload(audit: ServerAudit) -> dict[str, object]:
    entry: dict[str, object] = {
        "name": audit.name,
        "outcome": audit.outcome,
        # argv is echoed verbatim for provenance: an audit report that cannot say what it
        # ran is unfalsifiable. Secrets belong in `env`, which is never echoed.
        "command": list(audit.command),
        "duration_ms": audit.duration_ms,
    }
    if audit.report is not None:
        entry["report"] = report_payload(audit.report)
    if audit.error is not None:
        entry["error"] = audit.error
    return entry


def render_fleet_html(fleet: FleetReport) -> str:
    """Render a FleetReport as a single self-contained HTML document.

    Args:
        fleet: The combined report.

    Returns:
        HTML with no external references, matching the single-server report's constraint
        so it can be archived or opened offline.
    """
    esc = html.escape
    status_color = _STATUS_COLOR.get(fleet.status, "#111827")
    rows: list[str] = []
    for audit in fleet.audits:
        name = esc(_t(audit.name))
        mark = _OUTCOME_MARK.get(audit.outcome, audit.outcome)
        if audit.report is not None:
            color = _STATUS_COLOR.get(audit.report.status, "#111827")
            detail = (
                f"{esc(audit.report.status.value)} · {audit.report.summary.tools_scanned} tool(s) · "
                f"{audit.duration_ms} ms"
            )
            score = "-" if audit.report.summary.score is None else str(audit.report.summary.score)
            diag_rows = "".join(
                "<tr>"
                f'<td class="sev sev-{esc(d.severity.value)}">{esc(d.severity.value)}</td>'
                f"<td>{esc(d.rule_id)}</td>"
                f"<td>{esc(_t(d.tool_name))}</td>"
                f"<td>{esc(_t(d.message))}</td>"
                "</tr>"
                for d in audit.report.diagnostics
            )
            findings = (
                f'<table class="findings"><thead><tr><th>Severity</th><th>Rule</th>'
                f"<th>Tool</th><th>Finding</th></tr></thead><tbody>{diag_rows}</tbody></table>"
                if diag_rows
                else '<p class="okline">No findings on this server.</p>'
            )
        else:
            color = "#dc2626" if audit.outcome == OUTCOME_FAILED else "#6b7280"
            detail = esc(_t(audit.error or ""))
            score = "-"
            findings = ""
        rows.append(
            "<tr>"
            f'<td class="name">{name}</td>'
            f'<td><span class="pill" style="background:{color}">{esc(mark.strip())}</span></td>'
            f'<td class="num">{esc(score)}</td>'
            f"<td>{detail}</td>"
            "</tr>"
            f'<tr class="detail-row"><td colspan="4">{findings}</td></tr>'
        )

    worst = fleet.worst_server
    worst_line = (
        f"<p class='note'>The fleet score is the worst audited server "
        f"(<strong>{esc(_t(worst.name)) if worst else '-'}</strong>), not the average: "
        f"an average lets one vulnerable server hide behind healthy ones. "
        f"Mean across audited servers: {fleet.mean_score if fleet.mean_score is not None else 'n/a'}/100.</p>"
        if fleet.score is not None
        else "<p class='note'>Nothing was graded, so no fleet score is reported.</p>"
    )
    incomplete = (
        f"<p class='warn'><strong>Incomplete.</strong> {esc(_coverage_text(fleet))}. "
        "Findings below cover only the servers that were audited.</p>"
        if not fleet.complete
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vexar-MCP fleet report — {esc(_t(fleet.registry_path))}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0; padding: 32px; color: #111827; background: #f9fafb; }}
  main {{ max-width: 1000px; margin: 0 auto; background: #fff; padding: 28px 32px;
          border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: #6b7280; margin: 0 0 20px; font-size: 13px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 0 0 20px; }}
  .card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 16px; min-width: 150px; }}
  .card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #6b7280; }}
  .card .value {{ font-size: 22px; font-weight: 600; }}
  table.servers {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  table.servers th {{ text-align: left; font-size: 11px; text-transform: uppercase;
                      letter-spacing: .04em; color: #6b7280; padding: 6px 8px;
                      border-bottom: 2px solid #e5e7eb; }}
  table.servers td {{ padding: 8px; border-bottom: 1px solid #f3f4f6; vertical-align: top; }}
  td.name {{ font-weight: 600; }}
  td.num {{ font-variant-numeric: tabular-nums; }}
  .pill {{ color: #fff; border-radius: 999px; padding: 1px 9px; font-size: 11px;
           font-weight: 600; letter-spacing: .03em; }}
  .detail-row td {{ padding-top: 0; border-bottom: 1px solid #f3f4f6; }}
  table.findings {{ width: 100%; border-collapse: collapse; margin: 4px 0 10px; }}
  table.findings th {{ font-size: 10px; text-transform: uppercase; color: #6b7280;
                      text-align: left; padding: 4px 6px; }}
  table.findings td {{ padding: 4px 6px; font-size: 12px; border-top: 1px solid #f3f4f6; }}
  td.sev {{ font-weight: 600; text-transform: uppercase; font-size: 10px; }}
  td.sev-error {{ color: #dc2626; }}
  td.sev-warning {{ color: #d97706; }}
  td.sev-info {{ color: #0284c7; }}
  .warn {{ background: #fef2f2; border-left: 4px solid #dc2626; padding: 10px 14px;
           border-radius: 4px; }}
  .note {{ color: #374151; font-size: 12px; }}
  .okline {{ color: #16a34a; font-size: 12px; margin: 0 0 8px; }}
  footer {{ color: #9ca3af; font-size: 11px; margin-top: 24px; }}
</style>
</head>
<body>
<main>
  <h1>Vexar-MCP fleet report</h1>
  <p class="sub">{esc(_t(fleet.registry_path))} · format {esc(_t(fleet.source_format))} ·
     policy {esc(_t(fleet.policy_name))} · v{esc(__version__)}</p>
  <div class="cards">
    <div class="card"><div class="label">Fleet score</div>
      <div class="value" style="color:{status_color}">{esc(_fleet_score_text(fleet))}</div></div>
    <div class="card"><div class="label">Status</div>
      <div class="value" style="color:{status_color}">{esc(fleet.status.value.upper())}</div></div>
    <div class="card"><div class="label">Risk index</div>
      <div class="value">{fleet.risk_index}</div></div>
    <div class="card"><div class="label">Coverage</div>
      <div class="value">{len(fleet.audited)}/{len(fleet.audits)}</div></div>
  </div>
  {worst_line}
  {incomplete}
  <table class="servers">
    <thead><tr><th>Server</th><th>Result</th><th>Score</th><th>Detail</th></tr></thead>
    <tbody>
      {"".join(rows)}
    </tbody>
  </table>
  <footer>Generated by Vexar-MCP {esc(__version__)} · self-contained, no external assets.</footer>
</main>
</body>
</html>
"""
