"""Context-window token budget rules (MCP002)."""

from __future__ import annotations

from collections.abc import Sequence

from mcp_doctor.models import Category, Diagnostic, ScanConfig, Severity, ToolSpec
from mcp_doctor.rules.base import register_rule

# How many of the most expensive tools to name in a server-budget finding. Naming the
# largest contributors turns a warning into something a developer can act on.
_TOP_CONTRIBUTORS = 3


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


@register_rule(
    rule_id="MCP014",
    slug="excessive-server-tokens",
    severity=Severity.WARNING,
    category=Category.TOKENS,
    summary="Total context-window footprint of all tool definitions exceeds the server-wide token budget.",
    remediation=(
        "Trim or split the most expensive tools, or move rarely used tools behind a "
        "separate server so they are not loaded on every turn."
    ),
)
def check_server_token_budget(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    """Flag a server whose combined tool definitions exceed ``max_server_tokens``.

    Per-tool budgets (``MCP002``) cannot catch this: a server can hold many individually
    small tools and still spend most of the context window on its own catalogue.
    """
    if not tools or config.max_server_tokens <= 0:
        return []

    total = sum(tool.estimated_tokens for tool in tools)
    if total <= config.max_server_tokens:
        return []

    ranked = sorted(tools, key=lambda t: t.estimated_tokens, reverse=True)
    worst = ranked[:_TOP_CONTRIBUTORS]
    contributors = ", ".join(f"{t.name} ~{t.estimated_tokens}" for t in worst)
    over_by = total - config.max_server_tokens
    share = round(100 * total / config.max_server_tokens)

    # Report against the single most expensive tool's location so the finding is
    # navigable, while naming the whole contributor set in the message.
    anchor = worst[0]
    return [
        Diagnostic(
            rule_id="MCP014",
            rule_slug="excessive-server-tokens",
            severity=Severity.WARNING,
            category=Category.TOKENS,
            message=(
                f"Server tool catalogue costs ~{total} tokens across {len(tools)} tools, "
                f"exceeding the {config.max_server_tokens}-token server budget by "
                f"~{over_by} ({share}% of budget). Largest contributors: {contributors}."
            ),
            source_file=anchor.source_file,
            line=anchor.line,
            tool_name=anchor.name,
            remediation=(
                "Reduce description length, drop unreachable parameters, or split the "
                "server so rarely used tools are not in every system prompt."
            ),
        )
    ]
