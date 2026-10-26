"""vexar-mcp: Static analyzer, security scanner, and token auditor for MCP servers."""

from mcp_doctor.engine import scan_paths
from mcp_doctor.models import (
    Category,
    Diagnostic,
    ParameterSpec,
    ScanConfig,
    ScanReport,
    Severity,
    ToolSpec,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Category",
    "Diagnostic",
    "ParameterSpec",
    "ScanConfig",
    "ScanReport",
    "Severity",
    "ToolSpec",
    "scan_paths",
]
