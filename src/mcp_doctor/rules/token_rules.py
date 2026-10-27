"""Context-window token budget rules (MCP002)."""

from __future__ import annotations

from collections.abc import Sequence

from mcp_doctor.models import Category, Diagnostic, ScanConfig, Severity, ToolSpec
from mcp_doctor.rules.base import register_rule


@register_rule(
    rule_id="MCP002",
    slug="excessive-tool-tokens",
    severity=Severity.WARNING,
    category=Category.TOKENS,
    summary="Tool definition exceeds per-tool token budget, inflating system prompt cost on every turn.",
    remediation="Trim verbose examples/prose in tool description or adjust `max-tool-tokens` in `vexar-mcp.toml`.",
)
def check_excessive_tokens(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for tool in tools:
        if tool.estimated_tokens > config.max_tool_tokens:
            out.append(
                Diagnostic(
                    rule_id="MCP002",
                    rule_slug="excessive-tool-tokens",
                    severity=Severity.WARNING,
                    category=Category.TOKENS,
                    message=(
                        f"Tool '{tool.name}' consumes ~{tool.estimated_tokens} tokens "
                        f"(budget: {config.max_tool_tokens} tokens)."
                    ),
                    source_file=tool.source_file,
                    line=tool.line,
                    tool_name=tool.name,
                    remediation=(
                        "Keep tool descriptions concise (< 250 tokens) and move lengthy tutorials to MCP resources."
                    ),
                )
            )
    return out
