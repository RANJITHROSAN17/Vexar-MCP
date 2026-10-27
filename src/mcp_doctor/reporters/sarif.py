"""SARIF v2.1.0 output reporter for GitHub Code Scanning integration."""

from __future__ import annotations

import json

from mcp_doctor import __version__
from mcp_doctor.models import ScanReport, Severity
from mcp_doctor.rules import get_all_rules

_SARIF_LEVEL = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "note",
}


def render_sarif(report: ScanReport) -> str:
    """Serialize findings as a SARIF v2.1.0 document."""
    rules_meta = [
        {
            "id": r.rule_id,
            "name": r.slug,
            "shortDescription": {"text": r.summary},
            "help": {"text": r.remediation},
            "defaultConfiguration": {"level": _SARIF_LEVEL[r.severity]},
            "properties": {"tags": [r.category.value, "mcp"]},
        }
        for r in get_all_rules()
    ]

    results = [
        {
            "ruleId": d.rule_id,
            "level": _SARIF_LEVEL[d.severity],
            "message": {"text": f"[{d.tool_name}] {d.message}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": d.source_file.lstrip("./")},
                        "region": {"startLine": max(1, d.line)},
                    }
                }
            ],
        }
        for d in report.diagnostics
    ]

    sarif_doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Vexar-MCP",
                        "version": __version__,
                        "informationUri": "https://github.com/RANJITHROSAN17/Vexar-MCP",
                        "rules": rules_meta,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif_doc, indent=2)
