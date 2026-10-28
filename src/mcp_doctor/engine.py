"""Scan orchestration engine for vexar-mcp."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from mcp_doctor.loaders import load_json_manifest, load_python_file, load_typescript_file
from mcp_doctor.models import (
    Diagnostic,
    ScanConfig,
    ScanReport,
    ScanSummary,
    Severity,
    ToolSpec,
)
from mcp_doctor.risk import apply_risk
from mcp_doctor.rules import get_all_rules

_SUPPORTED_SUFFIXES = frozenset({".py", ".json", ".ts", ".js", ".jsx"})


def _is_excluded(path: Path, exclude_globs: tuple[str, ...]) -> bool:
    parts = set(path.parts)
    return path.name.endswith(".d.ts") or any(
        pattern in parts or path.match(pattern) for pattern in exclude_globs
    )


def discover_candidate_files(targets: Sequence[Path], config: ScanConfig) -> list[Path]:
    """Walk target files/directories and return candidate Python, JSON, TS, JS and JSX files."""
    discovered: list[Path] = []
    seen: set[Path] = set()

    for raw_target in targets:
        target = raw_target.resolve()
        if not target.exists():
            raise FileNotFoundError(f"Scan target does not exist: {raw_target}")

        if target.is_file():
            if (
                target.suffix in _SUPPORTED_SUFFIXES
                and target not in seen
                and not _is_excluded(target, config.exclude_globs)
            ):
                seen.add(target)
                discovered.append(raw_target)
            continue

        for child in sorted(raw_target.rglob("*")):
            if not child.is_file():
                continue
            if _is_excluded(child, config.exclude_globs):
                continue
            if child.suffix not in _SUPPORTED_SUFFIXES:
                continue
            resolved = child.resolve()
            if resolved not in seen:
                seen.add(resolved)
                discovered.append(child)

    return discovered


def _compute_grade(score: int) -> str:
    if score >= 97:
        return "A+"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 55:
        return "D"
    return "F"


def compute_score(errors: int, warnings: int, infos: int, has_tools: bool) -> int:
    """Return the 0-100 health score for a finding mix."""
    if not has_tools:
        return 100
    penalty = errors * 15 + warnings * 6 + infos * 2
    return max(0, 100 - penalty)


def _inline_suppressed(
    diagnostics: Sequence[Diagnostic], tools: Sequence[ToolSpec]
) -> list[Diagnostic]:
    """Drop findings silenced by in-source suppressions (per-line or per-tool)."""
    by_line: dict[tuple[str, int, str], frozenset[str]] = {
        (t.source_file, t.line, t.name): t.suppressed_rules for t in tools
    }
    by_tool: dict[tuple[str, str], frozenset[str]] = {}
    for t in tools:
        key = (t.source_file, t.name)
        by_tool[key] = by_tool.get(key, frozenset()) | t.suppressed_rules

    kept: list[Diagnostic] = []
    for diag in diagnostics:
        suppressed = by_line.get(
            (diag.source_file, diag.line, diag.tool_name), frozenset()
        ) | by_tool.get((diag.source_file, diag.tool_name), frozenset())
        if diag.rule_id in suppressed or "ALL" in suppressed:
            continue
        kept.append(diag)
    return kept


def assemble_report(
    tools: Sequence[ToolSpec],
    diagnostics: Sequence[Diagnostic],
    config: ScanConfig,
) -> ScanReport:
    """Score findings, attach risk, and build the final report.

    Shared by the file scanner and the live server auditor so both paths produce
    identical summaries, grades, and risk indices.
    """
    ordered = sorted(
        diagnostics, key=lambda d: (d.source_file, d.line, -d.severity.rank, d.rule_id)
    )
    scored, index = apply_risk(ordered)

    errors = sum(1 for d in scored if d.severity is Severity.ERROR)
    warnings = sum(1 for d in scored if d.severity is Severity.WARNING)
    infos = sum(1 for d in scored if d.severity is Severity.INFO)
    score = compute_score(errors, warnings, infos, bool(tools))
    total_tokens = sum(t.estimated_tokens for t in tools)
    files = len({t.source_file for t in tools})

    summary = ScanSummary(
        files_scanned=files,
        tools_scanned=len(tools),
        total_estimated_tokens=total_tokens,
        errors=errors,
        warnings=warnings,
        infos=infos,
        score=score,
        grade=_compute_grade(score),
        risk_index=index,
    )
    return ScanReport(
        tools=tuple(tools),
        diagnostics=scored,
        summary=summary,
        policy_name=config.policy_name,
    )


def scan_paths(targets: Sequence[Path], config: ScanConfig | None = None) -> ScanReport:
    """Scan Python/TypeScript/JavaScript servers and JSON tool manifests into a ScanReport."""
    effective_cfg = config or ScanConfig()
    files = discover_candidate_files(targets, effective_cfg)

    all_tools: list[ToolSpec] = []
    for file_path in files:
        loaded = (
            load_python_file(file_path)
            if file_path.suffix == ".py"
            else load_typescript_file(file_path)
            if file_path.suffix in (".ts", ".js", ".jsx")
            else load_json_manifest(file_path)
        )
        all_tools.extend(loaded)

    raw: list[Diagnostic] = []
    for rule in get_all_rules():
        if rule.rule_id in effective_cfg.ignored_rules:
            continue
        raw.extend(rule.check(all_tools, effective_cfg))

    return assemble_report(all_tools, _inline_suppressed(raw, all_tools), effective_cfg)


def scan_server(
    spec_command: Sequence[str], config: ScanConfig | None = None, *, timeout: float = 30.0
) -> ScanReport:
    """Introspect a live MCP server over stdio and scan its announced tool catalog."""
    from mcp_doctor.live import LiveServerSpec, introspect_server, protocol_diagnostics

    effective_cfg = config or ScanConfig()
    result = introspect_server(LiveServerSpec(command=tuple(spec_command), timeout=timeout))

    raw: list[Diagnostic] = list(protocol_diagnostics(result))
    for rule in get_all_rules():
        if rule.rule_id in effective_cfg.ignored_rules:
            continue
        raw.extend(rule.check(result.tools, effective_cfg))
    return assemble_report(result.tools, _inline_suppressed(raw, result.tools), effective_cfg)


__all__ = [
    "assemble_report",
    "compute_score",
    "discover_candidate_files",
    "scan_paths",
    "scan_server",
]
