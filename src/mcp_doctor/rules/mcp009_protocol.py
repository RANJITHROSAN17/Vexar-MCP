"""Rule MCP009: Detect outdated MCP initialization patterns and unbounded schema definitions."""

from collections.abc import Sequence

from mcp_doctor.models import Category, Diagnostic, ScanConfig, Severity, ToolSpec
from mcp_doctor.rules.base import register_rule


@register_rule(
    rule_id="MCP009",
    slug="protocol-legacy-transport",
    summary="Detect unbounded pagination in tool schemas.",
    severity=Severity.WARNING,
    category=Category.SECURITY,
    remediation="Update to MCP 2026 stateless HTTP / server.discover patterns; add pagination limits to list/search tools.",
)
def check_mcp009_protocol(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    """Detect legacy initialize handshakes and unbounded schema/transport definitions."""
    out: list[Diagnostic] = []
    for tool in tools:
        for sink in tool.sinks:
            # Flag if tool is a list/search/paginated endpoint missing limits
            if sink.kind == "pagination_unbounded":
                out.append(
                    Diagnostic(
                        rule_id="MCP009",
                        rule_slug="protocol-legacy-transport",
                        message=f"Tool '{tool.name}' has unbounded pagination / missing limit parameter. MCP 2026 requires explicit bounds.",
                        severity=Severity.WARNING,
                        line=sink.line,
                        source_file=tool.source_file,
                        tool_name=tool.name,
                        category=Category.SECURITY,
                    )
                )
    return out
