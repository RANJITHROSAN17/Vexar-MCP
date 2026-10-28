"""YAML tool manifest loader, backed by an optional PyYAML dependency.

Agent frameworks and declarative MCP gateways commonly keep tool schemas in YAML, so
reading ``*.yaml`` / ``*.yml`` closes a real blind spot. Two constraints shape this module.

**The dependency is optional.** Vexar-MCP ships with no runtime dependencies, so PyYAML is
an extra (``vexar-mcp[yaml]``) and is imported lazily. When it is missing the loader does
*not* quietly return no tools: a manifest-shaped file it cannot read becomes a
:class:`ParseIssue`, which marks the scan ``PARTIAL`` and withholds the grade. Silently
skipping a file full of vulnerable tools and then reporting A+ is the exact failure mode
this project's scoring model exists to prevent.

**Most YAML in a repository is not a tool manifest.** Every repository has CI workflows,
``docker-compose.yml``, ``mkdocs.yml`` and lockfiles. :func:`looks_like_tool_manifest`
gates directory walking on a cheap, deliberately conservative marker check, so unrelated
YAML is never parsed, never counted as a candidate, and never becomes a parse issue. A user
who names a YAML file explicitly bypasses the check — an explicit target is an instruction.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from mcp_doctor.loaders.json_manifest import extract_tool_candidates, parse_tool_entries
from mcp_doctor.models import IssueKind, ParseIssue, ToolSpec

YAML_SUFFIXES = frozenset({".yaml", ".yml"})

# How much of a file the marker check reads. Manifests put `tools:` and `inputSchema:` in
# their first few hundred bytes; a 64 KiB budget scans a large manifest's header without
# reading multi-megabyte YAML twice.
_SNIFF_LIMIT = 64 * 1024

# Conservative, indentation-bounded markers. The indent bound matters: a workflow step
# writing `        tools: cargo` at eight spaces is not a manifest, while a manifest's
# `tools:` sits at column 0 and a tool's `inputSchema:` at column 0-4 inside a list item.
_MANIFEST_MARKERS = (
    re.compile(r"^ {0,4}(?:- +)?tools\s*:", re.MULTILINE),
    re.compile(r"^ {0,4}(?:- +)?input_?[Ss]chema\s*:", re.MULTILINE),
)


class _LineDict(dict[str, Any]):
    """A mapping that remembers the line it started on.

    Subclassing ``dict`` keeps the decoded document identical for every consumer while
    carrying the one piece of information a plain parse throws away. Findings in a
    several-hundred-line manifest are useless if they all point at line 1.
    """

    line: int = 0


def _line_loader(yaml_module: Any) -> Any:
    """Build a ``SafeLoader`` subclass whose mappings record their source line.

    Built per call rather than at import time so the module can be imported without
    PyYAML installed.
    """
    # `type(...)` returns a class object; annotate it as Any so mypy can follow the
    # dynamically added constructor registration.
    loader: Any = type("LineSafeLoader", (yaml_module.SafeLoader,), {})

    def construct_mapping(instance: Any, node: Any) -> _LineDict:
        instance.flatten_mapping(node)  # honour `<<` merge keys before building the dict
        mapping = _LineDict(instance.construct_pairs(node))
        mapping.line = node.start_mark.line + 1
        return mapping

    loader.add_constructor("tag:yaml.org,2002:map", construct_mapping)
    return loader


def _entry_lines(candidates: list[Any]) -> list[int | None]:
    """Return the source line of each candidate entry, when the loader recorded one."""
    return [getattr(entry, "line", None) or None for entry in candidates]


def yaml_backend() -> ModuleType | None:
    """Return the ``yaml`` module, or ``None`` when PyYAML is not installed.

    Imported lazily so the package keeps working with zero runtime dependencies; callers
    must handle ``None`` rather than assuming the extra is present.
    """
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover - exercised by the missing-dependency test
        return None
    return cast(ModuleType, yaml)


def looks_like_tool_manifest(path: Path) -> bool:
    """Cheaply decide whether a YAML file is worth parsing as a tool manifest.

    Args:
        path: Candidate YAML file.

    Returns:
        ``True`` when the file's first 64 KiB contain a marker no ordinary configuration
        file uses, such as a top-level ``tools:`` key or an ``inputSchema:`` definition.
        Unreadable files return ``False``: the loader reports read failures properly when
        the file is actually scanned.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(_SNIFF_LIMIT)
    except OSError:
        return False
    return any(marker.search(head) for marker in _MANIFEST_MARKERS)


def _invalid_yaml_issue(path: Path, exc: Exception, yaml_module: ModuleType) -> ParseIssue:
    mark = getattr(exc, "problem_mark", None)
    line = mark.line + 1 if mark is not None else None
    problem = getattr(exc, "problem", None) or str(exc)
    return ParseIssue(
        source_file=str(path),
        kind=IssueKind.INVALID_YAML,
        message=f"invalid YAML: {problem}",
        line=line,
    )


def load_yaml_manifest(path: Path, *, issues: list[ParseIssue] | None = None) -> list[ToolSpec]:
    """Load MCP / OpenAI / Anthropic tool definitions from a YAML file.

    Parsing uses ``SafeLoader`` only. ``UnsafeLoader``/``FullLoader`` construct arbitrary
    Python objects from untrusted input, and a security scanner that executes the thing it
    is scanning is worse than no scanner.

    Args:
        path: YAML manifest to read.
        issues: Optional sink for analysis failures. A missing PyYAML, an unreadable file,
            or malformed YAML appends a :class:`ParseIssue` instead of returning an empty
            list, because a manifest that was not analyzed must not look like a clean one.

    Returns:
        Tool specifications in file order; empty when the document holds no tool entries.
    """
    yaml_module = yaml_backend()
    if yaml_module is None:
        if issues is not None:
            issues.append(
                ParseIssue(
                    source_file=str(path),
                    kind=IssueKind.MISSING_DEPENDENCY,
                    message=(
                        "PyYAML is not installed, so this YAML manifest was not analyzed. "
                        "Install it with: pip install 'vexar-mcp[yaml]'"
                    ),
                )
            )
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        if issues is not None:
            issues.append(
                ParseIssue(
                    source_file=str(path),
                    kind=IssueKind.UNREADABLE,
                    message=f"cannot read file: {exc}",
                )
            )
        return []

    try:
        # SafeLoader subclass: `safe_load` semantics (no arbitrary object construction),
        # plus the source line of every mapping.
        raw: Any = yaml_module.load(text, Loader=_line_loader(yaml_module))
    except yaml_module.YAMLError as exc:
        if issues is not None:
            issues.append(_invalid_yaml_issue(path, exc, yaml_module))
        return []

    candidates = extract_tool_candidates(raw)
    return parse_tool_entries(candidates, str(path), lines=_entry_lines(candidates))


__all__ = [
    "YAML_SUFFIXES",
    "load_yaml_manifest",
    "looks_like_tool_manifest",
    "yaml_backend",
]
