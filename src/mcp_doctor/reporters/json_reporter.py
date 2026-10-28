"""Structured JSON output reporter."""

from __future__ import annotations

import json

from mcp_doctor import __version__
from mcp_doctor.baseline import BaselineDelta, fingerprint
from mcp_doctor.models import ScanReport
from mcp_doctor.risk import risk_band


def _delta_payload(delta: BaselineDelta | None) -> dict[str, object] | None:
    if delta is None:
        return None
    return {
        "new": len(delta.new_findings),
        "resolved": len(delta.resolved),
        "unchanged": delta.unchanged,
        "new_findings": [
            {
                "rule_id": d.rule_id,
                "tool_name": d.tool_name,
                "source_file": d.source_file,
                "line": d.line,
                "severity": d.severity.value,
                "fingerprint": fingerprint(d),
            }
            for d in delta.new_findings
        ],
        "resolved_findings": [
            {
                "rule_id": entry.rule_id,
                "tool_name": entry.tool_name,
                "source_file": entry.source_file,
                "severity": entry.severity,
                "fingerprint": entry.fingerprint,
            }
            for entry in delta.resolved
        ],
    }


def render_json(report: ScanReport, delta: BaselineDelta | None = None) -> str:
    """Serialize a ScanReport as deterministic pretty-printed JSON.

    The payload is additive-only across minor releases: consumers may rely on existing
    keys never disappearing or changing type. ``schema`` is bumped only for breaking
    changes.
    """
    s = report.summary
    payload: dict[str, object] = {
        "schema": "vexar-mcp-report/v1",
        "version": __version__,
        "policy": {"name": report.policy_name},
        "summary": {
            "files_scanned": s.files_scanned,
            "tools_scanned": s.tools_scanned,
            "total_estimated_tokens": s.total_estimated_tokens,
            "errors": s.errors,
            "warnings": s.warnings,
            "infos": s.infos,
            "total_diagnostics": s.total_diagnostics,
            "score": s.score,
            "grade": s.grade,
            "risk_index": s.risk_index,
            "risk_band": risk_band(s.risk_index),
        },
        "tools": [
            {
                "name": t.name,
                "source_file": t.source_file,
                "line": t.line,
                "estimated_tokens": t.estimated_tokens,
                "parameter_count": len(t.parameters),
            }
            for t in report.tools
        ],
        "diagnostics": [
            {
                "rule_id": d.rule_id,
                "rule_slug": d.rule_slug,
                "severity": d.severity.value,
                "category": d.category.value,
                "tool_name": d.tool_name,
                "source_file": d.source_file,
                "line": d.line,
                "message": d.message,
                "remediation": d.remediation,
                "risk": d.risk,
                "fingerprint": fingerprint(d),
            }
            for d in report.diagnostics
        ],
    }
    delta_payload = _delta_payload(delta)
    if delta_payload is not None:
        payload["baseline"] = delta_payload
    return json.dumps(payload, indent=2)
