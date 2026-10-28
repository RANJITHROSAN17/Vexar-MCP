#!/usr/bin/env python3
"""Convert a Vexar-MCP JSON report into GitHub Action step outputs.

The composite action used to inline this logic as a shell heredoc, which cannot be
indented inside a YAML block scalar without breaking the document - one of the reasons
that block was fragile. A real script is testable and reviewable.

Usage:
    python .github/scripts/action_outputs.py report.json >> "$GITHUB_OUTPUT"

Prints ``key=value`` lines. ``score`` is empty when the scan had nothing to grade
(status ``partial``, ``no_matches`` or ``error``), which is how the action surfaces
"we could not measure this" instead of a misleading 100.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def outputs_from_report(data: dict[str, Any]) -> dict[str, str]:
    """Return the step-output mapping for a parsed report payload."""
    summary = data.get("summary") or {}
    coverage = data.get("coverage") or {}
    baseline = data.get("baseline") or {}
    policy = data.get("policy") or {}

    score = summary.get("score")
    return {
        "score": "" if score is None else str(score),
        "grade": str(summary.get("grade", "N/A")),
        "risk_index": str(summary.get("risk_index", 0)),
        "total_diagnostics": str(summary.get("total_diagnostics", 0)),
        "new_findings": str(baseline.get("new", 0)),
        "policy": str(policy.get("name", "default")),
        "status": str(data.get("status", "unknown")),
        "analysis_complete": str(bool(data.get("analysis_complete", True))).lower(),
        "files_failed": str(coverage.get("files_failed", 0)),
    }


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: action_outputs.py <report.json>", file=sys.stderr)
        return 2
    path = Path(argv[0])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"action_outputs: cannot read report {path}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print(f"action_outputs: report {path} is not a JSON object", file=sys.stderr)
        return 1

    for key, value in outputs_from_report(data).items():
        # A newline in a value would silently truncate the output block.
        print(f"{key}={value.replace(chr(10), ' ').replace(chr(13), ' ')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
