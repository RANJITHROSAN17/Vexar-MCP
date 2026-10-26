"""Scan orchestration engine for mcp-doctor."""

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
from mcp_doctor.rules import get_all_rules


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
                target.suffix in {".py", ".json", ".ts", ".js", ".jsx"}
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
            if child.suffix not in {".py", ".json", ".ts", ".js", ".jsx"}:
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


def scan_paths(targets: Sequence[Path], config: ScanConfig | None = None) -> ScanReport:
    """Scan Python/TypeScript/JavaScript servers and JSON tool manifests and return a complete ScanReport."""
    effective_cfg = config or ScanConfig()
    files = discover_candidate_files(targets, effective_cfg)

    all_tools: list[ToolSpec] = []
    files_with_tools = 0
    for file_path in files:
        loaded = (
            load_python_file(file_path)
            if file_path.suffix == ".py"
            else load_typescript_file(file_path)
            if file_path.suffix in (".ts", ".js", ".jsx")
            else load_json_manifest(file_path)
        )
        if loaded:
            files_with_tools += 1
            all_tools.extend(loaded)

    suppressed_by_tool: dict[tuple[str, int, str], frozenset[str]] = {
        (t.source_file, t.line, t.name): t.suppressed_rules for t in all_tools
    }
    tool_suppressions_by_name: dict[tuple[str, str], frozenset[str]] = {}
    for t in all_tools:
        key = (t.source_file, t.name)
        tool_suppressions_by_name[key] = (
            tool_suppressions_by_name.get(key, frozenset()) | t.suppressed_rules
        )

    diagnostics: list[Diagnostic] = []
    for rule in get_all_rules():
        if rule.rule_id in effective_cfg.ignored_rules:
            continue
        for diag in rule.check(all_tools, effective_cfg):
            inline_suppressed = suppressed_by_tool.get(
                (diag.source_file, diag.line, diag.tool_name), frozenset()
            ) | tool_suppressions_by_name.get((diag.source_file, diag.tool_name), frozenset())
            if diag.rule_id in inline_suppressed or "ALL" in inline_suppressed:
                continue
            diagnostics.append(diag)

    diagnostics.sort(key=lambda d: (d.source_file, d.line, -d.severity.rank, d.rule_id))

    errors = sum(1 for d in diagnostics if d.severity is Severity.ERROR)
    warnings = sum(1 for d in diagnostics if d.severity is Severity.WARNING)
    infos = sum(1 for d in diagnostics if d.severity is Severity.INFO)

    penalty = errors * 15 + warnings * 6 + infos * 2
    score = max(0, 100 - penalty) if all_tools else 100
    total_tokens = sum(t.estimated_tokens for t in all_tools)

    summary = ScanSummary(
        files_scanned=files_with_tools,
        tools_scanned=len(all_tools),
        total_estimated_tokens=total_tokens,
        errors=errors,
        warnings=warnings,
        infos=infos,
        score=score,
        grade=_compute_grade(score),
    )

    return ScanReport(
        tools=tuple(all_tools),
        diagnostics=tuple(diagnostics),
        summary=summary,
    )
