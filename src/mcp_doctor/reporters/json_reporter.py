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
        # `status` and `coverage` answer "did the analysis complete?"; `summary` and
        # `diagnostics` answer "what did it find?". Consumers must never infer the first
        # from the second - an empty diagnostics list on a PARTIAL scan means nothing.
        "status": report.status.value,
        "analysis_complete": report.is_complete,
        # Provenance for live audits: which revision was used, and whether it was agreed by
        # the initialize handshake or by server/discover. Absent (null) for static scans.
        "live": (
            {"protocol_version": report.live_protocol, "negotiation": report.live_mode}
            if report.live_protocol
            else None
        ),
        "coverage": {
            "candidate_files": report.coverage.candidate_files,
            "files_analyzed": report.coverage.files_analyzed,
            "files_failed": report.coverage.files_failed,
            "files_with_tools": report.coverage.files_with_tools,
            "tools_analyzed": report.coverage.tools_analyzed,
        },
        "summary": {
            "files_scanned": s.files_scanned,
            "tools_scanned": s.tools_scanned,
            "total_estimated_tokens": s.total_estimated_tokens,
            "errors": s.errors,
            "warnings": s.warnings,
            "infos": s.infos,
            "total_diagnostics": s.total_diagnostics,
            # null when nothing was analyzed: there is no honest score to report.
            "score": s.score,
            "grade": s.grade,
            "risk_index": s.risk_index,
            "risk_band": risk_band(s.risk_index),
        },
        "parse_issues": [
            {
                "kind": issue.kind.value,
                "source_file": issue.source_file,
                "line": issue.line,
                "message": issue.message,
            }
            for issue in report.parse_issues
        ],
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
