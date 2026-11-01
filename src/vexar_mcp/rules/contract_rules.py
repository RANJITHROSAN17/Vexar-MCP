"""Contract and naming quality rules (MCP001, MCP008)."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from vexar_mcp.models import Category, Diagnostic, ScanConfig, Severity, ToolSpec
from vexar_mcp.rules.base import register_rule

_SNAKE_OR_KEBAB_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_GENERIC_NAMES = frozenset(
    {"run", "do", "exec", "execute", "action", "tool", "helper", "handler", "process", "call"}
)


@register_rule(
    rule_id="MCP001",
    slug="missing-tool-description",
    severity=Severity.ERROR,
    category=Category.CONTRACT,
    summary="Tool is missing a meaningful description (< 12 chars), impairing LLM tool selection.",
    remediation="Add a clear docstring or `description` explaining when and how the agent should call this tool.",
)
def check_missing_description(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    del config
    out: list[Diagnostic] = []
    for tool in tools:
        cleaned = " ".join(tool.description.split())
        if len(cleaned) < 12:
            out.append(
                Diagnostic(
                    rule_id="MCP001",
                    rule_slug="missing-tool-description",
                    severity=Severity.ERROR,
                    category=Category.CONTRACT,
                    message=(
                        f"Tool '{tool.name}' has an empty or overly brief description "
                        f"({len(cleaned)} chars; minimum 12 recommended)."
                    ),
                    source_file=tool.source_file,
                    line=tool.line,
                    tool_name=tool.name,
                    remediation="Document purpose, inputs, and side effects in the tool docstring or `description`.",
                )
            )
    return out


@register_rule(
    rule_id="MCP008",
    slug="ambiguous-or-duplicate-tool-name",
    severity=Severity.ERROR,
    category=Category.CONTRACT,
    summary="Duplicate, generic, or non-standard tool name degrades LLM routing accuracy.",
    remediation="Use unique, action-oriented `snake_case` names (e.g. `list_cluster_pods` instead of `run`).",
)
def check_tool_names(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    del config
    out: list[Diagnostic] = []
    by_name: dict[str, list[ToolSpec]] = defaultdict(list)
    for tool in tools:
        by_name[tool.name].append(tool)

    for name, group in by_name.items():
        if len(group) > 1:
            locations = ", ".join(f"{t.source_file}:{t.line}" for t in group)
            for t in group[1:]:
                out.append(
                    Diagnostic(
                        rule_id="MCP008",
                        rule_slug="ambiguous-or-duplicate-tool-name",
                        severity=Severity.ERROR,
                        category=Category.CONTRACT,
                        message=f"Duplicate tool name '{name}' defined across [{locations}].",
                        source_file=t.source_file,
                        line=t.line,
                        tool_name=t.name,
                        remediation="Rename duplicate tools so every exposed tool identifier is unique.",
                    )
                )

    for tool in tools:
        lower = tool.name.lower()
        if lower in _GENERIC_NAMES or not _SNAKE_OR_KEBAB_RE.match(tool.name):
            out.append(
                Diagnostic(
                    rule_id="MCP008",
                    rule_slug="ambiguous-or-duplicate-tool-name",
                    severity=Severity.ERROR,
                    category=Category.CONTRACT,
                    message=(
                        f"Tool name '{tool.name}' is overly generic or violates `[a-z][a-z0-9_-]{{2,63}}`."
                    ),
                    source_file=tool.source_file,
                    line=tool.line,
                    tool_name=tool.name,
                    remediation="Use specific `verb_noun` `snake_case` naming such as `fetch_invoice_summary`.",
                )
            )
    return out
