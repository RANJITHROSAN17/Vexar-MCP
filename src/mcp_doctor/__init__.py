"""vexar-mcp: security, schema and token-cost analysis plus policy enforcement for MCP servers.

Public API:

* :func:`mcp_doctor.scan_paths` - static scan of files and directories
* :func:`mcp_doctor.scan_server` - live scan of a running MCP server over stdio
* :mod:`mcp_doctor.policies` - enforcement presets and the protected-rule invariant
* :mod:`mcp_doctor.baseline` - accepted-risk snapshots and delta gating
* :mod:`mcp_doctor.risk` - deterministic risk scoring
* :mod:`mcp_doctor.rules.plugin` - opt-in rule packs
"""

from mcp_doctor.engine import scan_paths, scan_server
from mcp_doctor.models import (
    Category,
    Diagnostic,
    ParameterSpec,
    ScanConfig,
    ScanReport,
    ScanSummary,
    Severity,
    ToolSpec,
)

__version__ = "0.2.4"

__all__ = [
    "__version__",
    "Category",
    "Diagnostic",
    "ParameterSpec",
    "ScanConfig",
    "ScanReport",
    "ScanSummary",
    "Severity",
    "ToolSpec",
    "scan_paths",
    "scan_server",
]
