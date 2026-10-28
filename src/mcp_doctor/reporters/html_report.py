"""Self-contained HTML report with filtering, scorecards and trend sparkline.

The output is a single file with zero external references - no CDN, no fonts, no
scripts from the network - so it can be archived as a compliance artefact, attached to
a release, opened offline, or rendered inside a sandboxed iframe. All interactivity is
inline vanilla JavaScript and degrades gracefully: without it the full findings table
is still readable.
"""

from __future__ import annotations

import html
import json
from collections.abc import Sequence

from mcp_doctor import __version__
from mcp_doctor.baseline import BaselineDelta, fingerprint
from mcp_doctor.models import ScanReport, Severity
from mcp_doctor.risk import risk_band

_SEVERITY_COLOR = {
    Severity.ERROR: "#dc2626",
    Severity.WARNING: "#d97706",
    Severity.INFO: "#0284c7",
}
_BAND_COLOR = {
    "critical": "#dc2626",
    "high": "#ea580c",
    "medium": "#d97706",
    "low": "#0284c7",
    "none": "#16a34a",
}


def _score_color(score: int) -> str:
    if score >= 90:
        return "#16a34a"
    if score >= 80:
        return "#65a30d"
    if score >= 70:
        return "#d97706"
    if score >= 55:
        return "#ea580c"
    return "#dc2626"


def _sparkline(history: Sequence[int]) -> str:
    """Render a dependency-free inline SVG sparkline of score history."""
    if len(history) < 2:
        return ""
    width, height, pad = 260, 56, 4
    lo, hi = min(history), max(history)
    span = max(1, hi - lo)
    step = (width - 2 * pad) / (len(history) - 1)
    points = [
        (
            pad + index * step,
            height - pad - ((value - lo) / span) * (height - 2 * pad),
        )
        for index, value in enumerate(history)
    ]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    last_x, last_y = points[-1]
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Score trend across {len(history)} scans">'
        f'<polyline fill="none" stroke="#2563eb" stroke-width="2" points="{poly}"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5" fill="#2563eb"/>'
        "</svg>"
    )


def _badge(text: str, color: str) -> str:
    return (
        f'<span class="badge" style="background:{color}1a;color:{color};'
        f'border-color:{color}55">{html.escape(text)}</span>'
    )


def render_html(
    report: ScanReport,
    delta: BaselineDelta | None = None,
    *,
    history: Sequence[int] | None = None,
    title: str = "Vexar-MCP Audit Report",
) -> str:
    """Render a ScanReport as a standalone, filterable HTML document.

    Args:
        report: The scan to render.
        delta: Optional baseline comparison to surface new/resolved findings.
        history: Optional prior health scores, oldest first, for the trend sparkline.
        title: Document title.

    Returns:
        A complete HTML5 document as a string.
    """
    s = report.summary
    band = risk_band(s.risk_index)
    score_color = _score_color(s.score)
    chart = _sparkline(list(history or []))

    delta_block = ""
    if delta is not None:
        delta_block = (
            '<div class="delta">'
            f'<div><span class="delta-num new">{len(delta.new_findings)}</span>'
            '<span class="delta-label">new</span></div>'
            f'<div><span class="delta-num fixed">{len(delta.resolved)}</span>'
            '<span class="delta-label">resolved</span></div>'
            f'<div><span class="delta-num">{delta.unchanged}</span>'
            '<span class="delta-label">unchanged</span></div>'
            "</div>"
        )

    severity_counts = {
        "error": s.errors,
        "warning": s.warnings,
        "info": s.infos,
    }

    rows: list[str] = []
    for diag in report.diagnostics:
        color = _SEVERITY_COLOR[diag.severity]
        rows.append(
            f"<tr data-severity='{diag.severity.value}' data-rule='{html.escape(diag.rule_id)}' "
            f"data-tool='{html.escape(diag.tool_name)}'>"
            f"<td><span class='dot' style='background:{color}'></span>"
            f"{diag.severity.value.upper()}</td>"
            f"<td class='mono'>{html.escape(diag.rule_id)}</td>"
            f"<td class='mono'>{html.escape(diag.tool_name)}</td>"
            f"<td class='mono dim'>{html.escape(diag.source_file)}:{diag.line}</td>"
            f"<td>{html.escape(diag.message)}</td>"
            f"<td class='risk'>{diag.risk}</td>"
            "</tr>"
        )

    tools_rows = "".join(
        "<tr><td class='mono'>{name}</td><td class='mono dim'>{file}</td>"
        "<td class='num'>{tokens}</td><td class='num'>{params}</td></tr>".format(
            name=html.escape(tool.name),
            file=html.escape(f"{tool.source_file}:{tool.line}"),
            tokens=tool.estimated_tokens,
            params=len(tool.parameters),
        )
        for tool in sorted(report.tools, key=lambda t: t.estimated_tokens, reverse=True)[:40]
    )

    findings_table = (
        "<table id='findings'>"
        "<thead><tr><th>Severity</th><th>Rule</th><th>Tool</th><th>Location</th>"
        "<th>Finding</th><th>Risk</th></tr></thead><tbody>"
        + (
            "".join(rows)
            or "<tr><td colspan='6' class='empty'>No findings — everything is clean.</td></tr>"
        )
        + "</tbody></table>"
    )

    findings_json = json.dumps(
        [
            {
                "rule_id": d.rule_id,
                "severity": d.severity.value,
                "tool": d.tool_name,
                "file": d.source_file,
                "line": d.line,
                "message": d.message,
                "remediation": d.remediation,
                "risk": d.risk,
                "fingerprint": fingerprint(d),
            }
            for d in report.diagnostics
        ]
    )
    # Hardened embedding: any literal `<`, `>` or `&` becomes a JSON escape so server-
    # controlled strings (tool names, messages) can never terminate the script element
    # or open an HTML comment, even in older parsers.
    findings_json = (
        findings_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 20px 64px;
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f6f7f9; color: #111827;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  header {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: baseline; justify-content: space-between; }}
  h1 {{ font-size: 22px; margin: 0 0 2px; letter-spacing: -0.01em; }}
  .sub {{ color: #6b7280; font-size: 13px; margin: 0; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(158px, 1fr)); gap: 12px; margin: 24px 0; }}
  .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px 16px; }}
  .card .label {{ font-size: 11px; letter-spacing: .07em; text-transform: uppercase; color: #6b7280; }}
  .card .value {{ font-size: 26px; font-weight: 650; letter-spacing: -0.02em; margin-top: 4px; }}
  .card .hint {{ font-size: 12px; color: #9ca3af; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; border: 1px solid;
            font-size: 11px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; }}
  .delta {{ display: flex; gap: 28px; background: #fff; border: 1px solid #e5e7eb;
            border-radius: 12px; padding: 14px 18px; margin-bottom: 20px; }}
  .delta-num {{ font-size: 22px; font-weight: 650; display: block; line-height: 1.1; }}
  .delta-num.new {{ color: #dc2626; }}
  .delta-num.fixed {{ color: #16a34a; }}
  .delta-label {{ font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: .06em; }}
  section {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 18px 20px; margin-bottom: 20px; }}
  section h2 {{ font-size: 15px; margin: 0 0 14px; letter-spacing: -0.01em; }}
  .toolbar {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; align-items: center; }}
  button {{ font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 999px;
            border: 1px solid #d1d5db; background: #fff; color: #374151; cursor: pointer; }}
  button[aria-pressed="true"] {{ background: #111827; color: #fff; border-color: #111827; }}
  input[type=search] {{ font: inherit; font-size: 13px; padding: 6px 12px; border: 1px solid #d1d5db;
                        border-radius: 999px; min-width: 220px; flex: 1; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
        color: #6b7280; border-bottom: 1px solid #e5e7eb; padding: 6px 8px; font-weight: 600; }}
  td {{ padding: 8px; border-bottom: 1px solid #f3f4f6; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }}
  .dim {{ color: #6b7280; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .risk {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }}
  .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }}
  .empty {{ text-align: center; color: #16a34a; padding: 24px; }}
  .spark {{ display: block; margin-top: 6px; }}
  footer {{ color: #9ca3af; font-size: 12px; text-align: center; margin-top: 28px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #0b0f16; color: #e5e7eb; }}
    .card, section, .delta {{ background: #111827; border-color: #1f2937; }}
    th {{ border-color: #1f2937; }}
    td {{ border-color: #1f2937; }}
    button {{ background: #111827; color: #e5e7eb; border-color: #374151; }}
    button[aria-pressed="true"] {{ background: #e5e7eb; color: #111827; }}
    input[type=search] {{ background: #111827; color: #e5e7eb; border-color: #374151; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>{html.escape(title)}</h1>
      <p class="sub">Generated by Vexar-MCP {html.escape(__version__)} ·
        policy <strong>{html.escape(report.policy_name)}</strong> ·
        {s.tools_scanned} tool(s) across {s.files_scanned} file(s)</p>
    </div>
    {_badge(f"risk: {band}", _BAND_COLOR[band])}
  </header>

  <div class="cards">
    <div class="card">
      <div class="label">Health score</div>
      <div class="value" style="color:{score_color}">{s.score}<span class="hint">/100</span></div>
      <div class="hint">grade {html.escape(s.grade)}</div>
    </div>
    <div class="card">
      <div class="label">Risk index</div>
      <div class="value" style="color:{_BAND_COLOR[band]}">{s.risk_index}</div>
      <div class="hint">{band}</div>
    </div>
    <div class="card">
      <div class="label">Errors</div>
      <div class="value" style="color:#dc2626">{s.errors}</div>
      <div class="hint">must fix</div>
    </div>
    <div class="card">
      <div class="label">Warnings</div>
      <div class="value" style="color:#d97706">{s.warnings}</div>
      <div class="hint">review</div>
    </div>
    <div class="card">
      <div class="label">Info</div>
      <div class="value" style="color:#0284c7">{s.infos}</div>
      <div class="hint">advisory</div>
    </div>
    <div class="card">
      <div class="label">Context tax</div>
      <div class="value">{s.total_estimated_tokens}</div>
      <div class="hint">tokens / turn</div>
      {chart}
    </div>
  </div>

  {delta_block}

  <section>
    <h2>Findings</h2>
    <div class="toolbar">
      <button data-filter="all" aria-pressed="true">All ({s.total_diagnostics})</button>
      <button data-filter="error" aria-pressed="false">Errors ({severity_counts["error"]})</button>
      <button data-filter="warning" aria-pressed="false">Warnings ({severity_counts["warning"]})</button>
      <button data-filter="info" aria-pressed="false">Info ({severity_counts["info"]})</button>
      <input type="search" id="q" placeholder="Filter by rule, tool, file or message…" aria-label="Filter findings">
    </div>
    {findings_table}
  </section>

  <section>
    <h2>Tool catalogue · ranked by context cost</h2>
    <table>
      <thead><tr><th>Tool</th><th>Location</th><th class="num">Est. tokens</th><th class="num">Params</th></tr></thead>
      <tbody>{tools_rows or "<tr><td colspan='4' class='empty'>No tools discovered.</td></tr>"}</tbody>
    </table>
  </section>

  <footer>Static analysis only — Vexar-MCP never executes scanned code.
  Findings are also available as JSON for machine consumption.</footer>
</div>
<script id="findings-data" type="application/json">{findings_json}</script>
<script>
(function () {{
  var table = document.getElementById('findings');
  if (!table) return;
  var rows = Array.prototype.slice.call(table.tBodies[0].rows);
  var buttons = Array.prototype.slice.call(document.querySelectorAll('button[data-filter]'));
  var search = document.getElementById('q');
  var active = 'all';

  function apply() {{
    var term = (search && search.value || '').toLowerCase();
    rows.forEach(function (row) {{
      var okSeverity = active === 'all' || row.getAttribute('data-severity') === active;
      var text = row.textContent.toLowerCase();
      var okTerm = !term || text.indexOf(term) !== -1;
      row.style.display = (okSeverity && okTerm) ? '' : 'none';
    }});
  }}
  buttons.forEach(function (button) {{
    button.addEventListener('click', function () {{
      active = button.getAttribute('data-filter');
      buttons.forEach(function (other) {{
        other.setAttribute('aria-pressed', String(other === button));
      }});
      apply();
    }});
  }});
  if (search) search.addEventListener('input', apply);
}})();
</script>
</body>
</html>
"""
