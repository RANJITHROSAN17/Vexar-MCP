"""Scan orchestration engine for vexar-mcp.

Two invariants drive this module:

1. **Discovery must not explore what it will not scan.** Directory exclusions prune the
   walk instead of filtering its results, so scanning a monorepo does not spend its time
   enumerating ``node_modules``.
2. **Incomplete analysis is never reported as clean.** A file that fails to parse becomes
   a :class:`~mcp_doctor.models.ParseIssue` and lowers the scan status to ``PARTIAL``; the
   score becomes ``None`` when there was nothing to grade.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from mcp_doctor.loaders import load_json_manifest, load_python_file, load_typescript_file
from mcp_doctor.models import (
    Diagnostic,
    ParseIssue,
    ScanConfig,
    ScanCoverage,
    ScanReport,
    ScanStatus,
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


def _is_excluded_dir(name: str, full_path: Path, exclude_globs: tuple[str, ...]) -> bool:
    """Decide whether a directory should be pruned from the walk."""
    if name in exclude_globs:
        return True
    if any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(str(full_path), pattern)
        for pattern in exclude_globs
    ):
        return True
    # Always prune VCS and cache directories, even if a user replaces the exclude list,
    # because walking them is never useful and can be enormous.
    return name in {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }


def discover_candidate_files(targets: Sequence[Path], config: ScanConfig) -> list[Path]:
    """Walk target files/directories and return candidate Python, JSON, TS, JS and JSX files.

    Excluded directories are pruned during traversal rather than filtered afterwards, so a
    repository with a large dependency tree is not walked at all.
    """
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

        for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
            directory = Path(dirpath)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not _is_excluded_dir(name, directory / name, config.exclude_globs)
            )
            for name in sorted(filenames):
                candidate = directory / name
                if candidate.suffix not in _SUPPORTED_SUFFIXES:
                    continue
                if _is_excluded(candidate, config.exclude_globs):
                    continue
                resolved = candidate.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                discovered.append(candidate)

    return discovered


def _compute_grade(score: int | None) -> str:
    if score is None:
        return "N/A"
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


def compute_score(errors: int, warnings: int, infos: int, has_tools: bool) -> int | None:
    """Return the 0-100 quality score, or ``None`` when there was nothing to grade.

    A perfect score must mean "analyzed everything, nothing fired". Returning ``100`` for
    an empty or unparsable scan was the single most dangerous behaviour in this project:
    it let a repository with a syntax error report an A+.
    """
    if not has_tools:
        return None
    penalty = errors * 15 + warnings * 6 + infos * 2
    return max(0, 100 - penalty)


def determine_status(
    *,
    candidate_files: int,
    files_failed: int,
    tools: Sequence[ToolSpec],
    diagnostics: Sequence[Diagnostic],
) -> ScanStatus:
    """Classify whether the requested analysis actually completed."""
    analyzed = candidate_files - files_failed
    if candidate_files > 0 and analyzed <= 0:
        return ScanStatus.ERROR
    if files_failed > 0:
        return ScanStatus.PARTIAL
    if not tools:
        return ScanStatus.NO_MATCHES
    if diagnostics:
        return ScanStatus.FINDINGS
    return ScanStatus.CLEAN


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
    *,
    coverage: ScanCoverage | None = None,
    parse_issues: Sequence[ParseIssue] = (),
) -> ScanReport:
    """Score findings, attach risk, and build the final report.

    Shared by the file scanner and the live server auditor so both paths produce identical
    summaries, grades, statuses, and risk indices.
    """
    ordered = sorted(
        diagnostics, key=lambda d: (d.source_file, d.line, -d.severity.rank, d.rule_id)
    )
    scored, index = apply_risk(ordered)

    errors = sum(1 for d in scored if d.severity is Severity.ERROR)
    warnings = sum(1 for d in scored if d.severity is Severity.WARNING)
    infos = sum(1 for d in scored if d.severity is Severity.INFO)
    total_tokens = sum(t.estimated_tokens for t in tools)

    effective_coverage = coverage or ScanCoverage(
        candidate_files=len({t.source_file for t in tools}),
        files_analyzed=len({t.source_file for t in tools}),
        files_failed=0,
        files_with_tools=len({t.source_file for t in tools}),
        tools_analyzed=len(tools),
    )

    status = determine_status(
        candidate_files=effective_coverage.candidate_files,
        files_failed=effective_coverage.files_failed,
        tools=tools,
        diagnostics=scored,
    )

    # A grade is a claim about the codebase. Withholding it when part of the codebase was
    # never read is the whole point of tracking status: an "A+" beside "PARTIAL" is exactly
    # the false confidence this model exists to prevent. Counts are still reported.
    score = compute_score(errors, warnings, infos, bool(tools)) if status.is_complete else None

    summary = ScanSummary(
        files_scanned=effective_coverage.files_analyzed,
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
        status=status,
        coverage=effective_coverage,
        parse_issues=tuple(parse_issues),
    )


def scan_paths(targets: Sequence[Path], config: ScanConfig | None = None) -> ScanReport:
    """Scan Python/TypeScript/JavaScript servers and JSON tool manifests into a ScanReport."""
    effective_cfg = config or ScanConfig()
    files = discover_candidate_files(targets, effective_cfg)

    all_tools: list[ToolSpec] = []
    parse_issues: list[ParseIssue] = []
    files_with_tools = 0

    for file_path in files:
        loaded = (
            load_python_file(file_path, issues=parse_issues)
            if file_path.suffix == ".py"
            else load_typescript_file(file_path, issues=parse_issues)
            if file_path.suffix in (".ts", ".js", ".jsx")
            else load_json_manifest(file_path, issues=parse_issues)
        )
        if loaded:
            files_with_tools += 1
        all_tools.extend(loaded)

    raw: list[Diagnostic] = []
    for rule in get_all_rules():
        if rule.rule_id in effective_cfg.ignored_rules:
            continue
        raw.extend(rule.check(all_tools, effective_cfg))

    coverage = ScanCoverage(
        candidate_files=len(files),
        files_analyzed=len(files) - len(parse_issues),
        files_failed=len(parse_issues),
        files_with_tools=files_with_tools,
        tools_analyzed=len(all_tools),
    )
    return assemble_report(
        all_tools,
        _inline_suppressed(raw, all_tools),
        effective_cfg,
        coverage=coverage,
        parse_issues=parse_issues,
    )


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
    report = assemble_report(result.tools, _inline_suppressed(raw, result.tools), effective_cfg)
    return replace(report, live_protocol=result.protocol_version, live_mode=result.mode)


__all__ = [
    "assemble_report",
    "compute_score",
    "determine_status",
    "discover_candidate_files",
    "scan_paths",
    "scan_server",
]
