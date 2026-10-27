"""Core typed domain models for mcp-doctor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """Diagnostic severity level."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        ranks = {Severity.INFO: 1, Severity.WARNING: 2, Severity.ERROR: 3}
        return ranks[self]


class Category(str, Enum):
    """Diagnostic rule category."""

    SECURITY = "security"
    TOKENS = "tokens"
    SCHEMA = "schema"
    CONTRACT = "contract"


@dataclass(frozen=True)
class ParameterSpec:
    """Normalized representation of a tool input parameter."""

    name: str
    type_name: str | None = None
    description: str = ""
    required: bool = True
    has_bounds: bool = False
    line: int = 1


@dataclass(frozen=True)
class CodeSink:
    """Potentially sensitive operation discovered inside a Python tool body."""

    kind: str  # "shell", "ssrf", "filesystem"
    call_name: str
    line: int
    uses_param: str | None = None
    validated: bool = False
    timeout_missing: bool = False


@dataclass(frozen=True)
class SecretLiteral:
    """Metadata about a hardcoded credential literal found in a Python tool body.

    MCP009 keeps only the *shape* of the finding (matched pattern, receiving name,
    source line). The raw secret value is deliberately never stored, so diagnostics,
    JSON reports and SARIF uploads cannot become a second copy of the leaked key.
    """

    pattern: str
    target: str
    line: int


@dataclass(frozen=True)
class ToolSpec:
    """Normalized representation of an MCP or AI agent tool definition."""

    name: str
    description: str
    source_file: str
    line: int
    parameters: tuple[ParameterSpec, ...] = ()
    sinks: tuple[CodeSink, ...] = ()
    # MCP009: credential literals extracted from a Python tool body; empty for other loaders.
    secret_literals: tuple[SecretLiteral, ...] = ()
    suppressed_rules: frozenset[str] = field(default_factory=frozenset)
    raw_schema: dict[str, Any] = field(default_factory=dict)
    estimated_tokens: int = 0


@dataclass(frozen=True)
class Diagnostic:
    """Single lint or security finding emitted by a rule."""

    rule_id: str
    rule_slug: str
    severity: Severity
    category: Category
    message: str
    source_file: str
    line: int
    tool_name: str
    remediation: str = ""


@dataclass(frozen=True)
class ScanConfig:
    """Effective configuration for a scan run."""

    max_tool_tokens: int = 250
    max_server_tokens: int = 4000
    ignored_rules: frozenset[str] = field(default_factory=frozenset)
    exclude_globs: tuple[str, ...] = (
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
    )


@dataclass(frozen=True)
class ScanSummary:
    """Aggregated scorecard and diagnostic counts for a scan."""

    files_scanned: int
    tools_scanned: int
    total_estimated_tokens: int
    errors: int
    warnings: int
    infos: int
    score: int
    grade: str

    @property
    def total_diagnostics(self) -> int:
        return self.errors + self.warnings + self.infos


@dataclass(frozen=True)
class ScanReport:
    """Full scan result passed to output reporters."""

    tools: tuple[ToolSpec, ...]
    diagnostics: tuple[Diagnostic, ...]
    summary: ScanSummary
