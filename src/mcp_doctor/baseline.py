"""Baseline comparison: adopt Vexar-MCP on a codebase that is already dirty.

A gate nobody can turn on is a gate nobody uses. Teams with hundreds of existing
findings need to *stop the bleeding* first, then burn the backlog down. Baselines make
that possible: commit one snapshot, and CI only fails on findings introduced after it.

Fingerprints deliberately **exclude line numbers and detail text**. Including them
would make every unrelated edit look like a brand-new violation, which trains teams to
ignore the tool. A finding is identified by what it *is*:

    fingerprint = sha256(rule_id | tool_name | normalized_source_file)

The baseline file is versioned JSON so it can be reviewed, diffed and committed
alongside the code it describes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_doctor.models import Diagnostic, ScanReport

BASELINE_SCHEMA = "vexar-mcp-baseline/v1"


class BaselineError(ValueError):
    """Raised when a baseline file is missing, malformed, or from a newer schema."""


@dataclass(frozen=True)
class BaselineEntry:
    """One accepted-with-known-risk finding recorded in a baseline."""

    fingerprint: str
    rule_id: str
    tool_name: str
    source_file: str
    severity: str


@dataclass(frozen=True)
class Baseline:
    """A committed snapshot of accepted findings."""

    entries: tuple[BaselineEntry, ...]
    created_at: str = ""
    tool_version: str = ""
    policy: str = ""
    schema: str = BASELINE_SCHEMA

    @property
    def fingerprints(self) -> frozenset[str]:
        """Return just the fingerprint set for fast membership checks."""
        return frozenset(entry.fingerprint for entry in self.entries)


@dataclass(frozen=True)
class BaselineDelta:
    """Comparison between an accepted baseline and the current scan."""

    new_findings: tuple[Diagnostic, ...]
    resolved: tuple[BaselineEntry, ...]
    unchanged: int

    def new_at_or_above(self, min_severity_rank: int) -> tuple[Diagnostic, ...]:
        """Return new findings whose severity rank meets the gate threshold."""
        return tuple(d for d in self.new_findings if d.severity.rank >= min_severity_rank)


def normalize_source(path: str) -> str:
    """Normalize a source path so baselines survive repo-root and OS differences."""
    cleaned = path.replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def fingerprint(diagnostic: Diagnostic) -> str:
    """Compute the stable identity of a finding."""
    payload = "|".join(
        (
            diagnostic.rule_id,
            diagnostic.tool_name,
            normalize_source(diagnostic.source_file),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def build_baseline(report: ScanReport, *, policy: str = "", tool_version: str = "") -> Baseline:
    """Capture every current finding as accepted risk."""
    seen: dict[str, BaselineEntry] = {}
    for diagnostic in report.diagnostics:
        fp = fingerprint(diagnostic)
        seen.setdefault(
            fp,
            BaselineEntry(
                fingerprint=fp,
                rule_id=diagnostic.rule_id,
                tool_name=diagnostic.tool_name,
                source_file=normalize_source(diagnostic.source_file),
                severity=diagnostic.severity.value,
            ),
        )
    return Baseline(
        entries=tuple(seen[key] for key in sorted(seen)),
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        tool_version=tool_version,
        policy=policy,
    )


def compare(baseline: Baseline, report: ScanReport) -> BaselineDelta:
    """Diff a baseline against the current scan."""
    accepted = baseline.fingerprints
    current: dict[str, Diagnostic] = {}
    for diagnostic in report.diagnostics:
        current.setdefault(fingerprint(diagnostic), diagnostic)

    new = tuple(current[fp] for fp in sorted(current) if fp not in accepted)
    resolved = tuple(entry for entry in baseline.entries if entry.fingerprint not in current)
    unchanged = sum(1 for fp in current if fp in accepted)
    return BaselineDelta(new_findings=new, resolved=resolved, unchanged=unchanged)


def baseline_to_json(baseline: Baseline) -> str:
    """Serialize a baseline deterministically for committing to source control."""
    payload: dict[str, Any] = {
        "schema": baseline.schema,
        "tool_version": baseline.tool_version,
        "policy": baseline.policy,
        "created_at": baseline.created_at,
        "entry_count": len(baseline.entries),
        "entries": [
            {
                "fingerprint": entry.fingerprint,
                "rule_id": entry.rule_id,
                "tool_name": entry.tool_name,
                "source_file": entry.source_file,
                "severity": entry.severity,
            }
            for entry in baseline.entries
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def baseline_from_json(text: str) -> Baseline:
    """Parse a baseline document, failing closed on anything unexpected."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"baseline is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise BaselineError("baseline root must be a JSON object")

    schema = doc.get("schema", BASELINE_SCHEMA)
    if schema != BASELINE_SCHEMA:
        raise BaselineError(
            f"unsupported baseline schema '{schema}'. This build understands '{BASELINE_SCHEMA}'."
        )

    raw_entries = doc.get("entries", [])
    if not isinstance(raw_entries, list):
        raise BaselineError("baseline 'entries' must be a list")

    entries: list[BaselineEntry] = []
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise BaselineError(f"baseline entry {index} must be an object")
        missing = [
            key for key in ("fingerprint", "rule_id", "tool_name", "source_file") if key not in raw
        ]
        if missing:
            raise BaselineError(
                f"baseline entry {index} is missing required field(s): {', '.join(missing)}"
            )
        entries.append(
            BaselineEntry(
                fingerprint=str(raw["fingerprint"]),
                rule_id=str(raw["rule_id"]),
                tool_name=str(raw["tool_name"]),
                source_file=str(raw["source_file"]),
                severity=str(raw.get("severity", "warning")),
            )
        )

    return Baseline(
        entries=tuple(entries),
        created_at=str(doc.get("created_at", "")),
        tool_version=str(doc.get("tool_version", "")),
        policy=str(doc.get("policy", "")),
        schema=schema,
    )


def write_baseline(path: Path, baseline: Baseline) -> None:
    """Write a baseline file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(baseline_to_json(baseline), encoding="utf-8")


def read_baseline(path: Path) -> Baseline:
    """Read a baseline file from disk.

    Raises:
        BaselineError: When the file is missing or unreadable.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BaselineError(
            f"baseline file not found: {path}. Create one with `vexar-mcp baseline write`."
        ) from exc
    except OSError as exc:
        raise BaselineError(f"cannot read baseline file {path}: {exc}") from exc
    return baseline_from_json(text)
