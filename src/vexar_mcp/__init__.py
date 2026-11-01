"""vexar-mcp: security, schema and token-cost analysis plus policy enforcement for MCP servers.

Public API:

* :func:`vexar_mcp.scan_paths` - static scan of files and directories
* :func:`vexar_mcp.scan_server` - live scan of a running MCP server over stdio
* :mod:`vexar_mcp.policies` - enforcement presets and the protected-rule invariant
* :mod:`vexar_mcp.baseline` - accepted-risk snapshots and delta gating
* :mod:`vexar_mcp.risk` - deterministic risk scoring
* :mod:`vexar_mcp.rules.plugin` - opt-in rule packs
"""

from vexar_mcp.engine import scan_paths, scan_server
from vexar_mcp.models import (
    Category,
    Diagnostic,
    ParameterSpec,
    ScanConfig,
    ScanReport,
    ScanSummary,
    Severity,
    ToolSpec,
)

__version__ = "0.3.0"

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
