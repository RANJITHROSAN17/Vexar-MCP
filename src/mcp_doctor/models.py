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


class ScanStatus(str, Enum):
    """Whether the requested analysis actually completed.

    This is deliberately separate from findings. A file that cannot be parsed is an
    *analysis failure*, not a security result, and conflating the two is how a scanner
    reports a confident clean score on code it never read.

    * ``CLEAN`` - every input was analyzed and nothing fired
    * ``FINDINGS`` - every input was analyzed and at least one rule fired
    * ``PARTIAL`` - some inputs could not be analyzed; results are incomplete
    * ``NO_MATCHES`` - inputs were read but no supported MCP tool was recognized
    * ``ERROR`` - nothing could be analyzed at all
    """

    CLEAN = "clean"
    FINDINGS = "findings"
    PARTIAL = "partial"
    NO_MATCHES = "no_matches"
    ERROR = "error"

    @property
    def is_complete(self) -> bool:
        """True when the scan analyzed everything it was asked to."""
        return self in {ScanStatus.CLEAN, ScanStatus.FINDINGS, ScanStatus.NO_MATCHES}


class IssueKind(str, Enum):
    """Why a candidate file could not be analyzed."""

    SYNTAX_ERROR = "syntax_error"
    INVALID_JSON = "invalid_json"
    UNREADABLE = "unreadable"


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

    kind: str  # "shell", "ssrf", "filesystem", "deserialization", "sql"
    call_name: str
    line: int
    uses_param: str | None = None
    validated: bool = False
    timeout_missing: bool = False


@dataclass(frozen=True)
class ParseIssue:
    """A candidate file that could not be analyzed.

    Reported separately from :class:`Diagnostic`. Parse issues never produce a security
    finding and never contribute to a score - they exist so that incomplete analysis is
    visible instead of looking like a clean result.
    """

    source_file: str
    kind: IssueKind
    message: str
    line: int | None = None


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
    # Deterministic 0-100 risk score (severity x exploitability x mitigation), see mcp_doctor.risk.
    risk: int = 0


@dataclass(frozen=True)
class ScanConfig:
    """Effective configuration for a scan run."""

    max_tool_tokens: int = 250
    max_server_tokens: int = 4000
    ignored_rules: frozenset[str] = field(default_factory=frozenset)
    # Active policy preset name, surfaced in every reporter for auditability.
    policy_name: str = "default"
    # Minimum diagnostic severity that fails the run; None means "never fail".
    fail_on: Severity | None = Severity.ERROR
    # Minimum acceptable health score; a lower score fails the run.
    min_score: int = 0
    # Opt-in, explicitly trusted third-party rule packs (module paths or TOML/JSON files).
    rule_packs: tuple[str, ...] = ()
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
class ScanCoverage:
    """What the scanner actually looked at.

    Every counter answers a different question, and none of them is a substitute for
    another: 40 candidate files with 0 parsed is a very different situation from 40
    candidate files with 40 parsed and 0 tools.
    """

    candidate_files: int = 0
    files_analyzed: int = 0
    files_failed: int = 0
    files_with_tools: int = 0
    tools_analyzed: int = 0


@dataclass(frozen=True)
class ScanSummary:
    """Aggregated scorecard and diagnostic counts for a scan.

    ``score`` is ``None`` when no tools were analyzed: there is nothing to grade, and
    inventing a number there is exactly the failure mode this model exists to prevent.
    """

    files_scanned: int
    tools_scanned: int
    total_estimated_tokens: int
    errors: int
    warnings: int
    infos: int
    score: int | None
    grade: str
    # Aggregate risk index (0-100, higher = riskier), sum of per-finding risk capped at 100.
    risk_index: int = 0

    @property
    def total_diagnostics(self) -> int:
        return self.errors + self.warnings + self.infos


@dataclass(frozen=True)
class ScanReport:
    """Full scan result passed to output reporters."""

    tools: tuple[ToolSpec, ...]
    diagnostics: tuple[Diagnostic, ...]
    summary: ScanSummary
    # Enforcement profile this report was produced under, so results are self-describing.
    policy_name: str = "default"
    # Whether the analysis completed, and what it covered. Never infer this from findings.
    status: ScanStatus = ScanStatus.CLEAN
    coverage: ScanCoverage = field(default_factory=ScanCoverage)
    parse_issues: tuple[ParseIssue, ...] = ()
    #: Set only for live audits: the protocol revision actually used and how it was agreed.
    live_protocol: str | None = None
    live_mode: str | None = None

    @property
    def is_complete(self) -> bool:
        """True when every candidate file was analyzed."""
        return self.status.is_complete
