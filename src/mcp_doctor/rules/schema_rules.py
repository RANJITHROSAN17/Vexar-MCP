"""Input schema typing, bounds and documentation rules (MCP003, MCP004, MCP015)."""

from __future__ import annotations

from collections.abc import Sequence

from mcp_doctor.models import Category, Diagnostic, ScanConfig, Severity, ToolSpec
from mcp_doctor.rules.base import register_rule

_WEAK_TYPES = frozenset({"Any", "typing.Any", "object", "dict", "list"})
_PAGINATION_OR_BATCH_NAMES = frozenset(
    {"limit", "page_size", "max_results", "top_k", "count", "items", "ids", "urls", "queries"}
)


@register_rule(
    rule_id="MCP003",
    slug="untyped-or-any-parameter",
    severity=Severity.WARNING,
    category=Category.SCHEMA,
    summary="Tool parameter lacks a concrete type or uses unconstrained `Any` / bare `dict`.",
    remediation="Annotate every tool parameter with concrete Python types or JSON Schema `type` constraints.",
)
def check_untyped_parameters(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    del config
    out: list[Diagnostic] = []
    for tool in tools:
        for param in tool.parameters:
            tname = (param.type_name or "").strip()
            if not tname or tname in _WEAK_TYPES:
                reason = (
                    "has no type annotation" if not tname else f"uses unconstrained type '{tname}'"
                )
                out.append(
                    Diagnostic(
                        rule_id="MCP003",
                        rule_slug="untyped-or-any-parameter",
                        severity=Severity.WARNING,
                        category=Category.SCHEMA,
                        message=f"Parameter '{param.name}' in tool '{tool.name}' {reason}.",
                        source_file=tool.source_file,
                        line=param.line or tool.line,
                        tool_name=tool.name,
                        remediation="Provide an explicit scalar, `Literal[...]`, `TypedDict`, or Pydantic `BaseModel` type.",
                    )
                )
    return out


@register_rule(
    rule_id="MCP004",
    slug="unbounded-collection-or-limit",
    severity=Severity.INFO,
    category=Category.SCHEMA,
    summary="Pagination/batch/list parameter has no upper bound, risking context-window output floods.",
    remediation="Add `Field(le=...)`, `maxItems`, `maximum`, or clamp the value with `min(limit, MAX_LIMIT)`.",
)
def check_unbounded_parameters(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    del config
    out: list[Diagnostic] = []
    for tool in tools:
        for param in tool.parameters:
            if param.has_bounds:
                continue
            tname = (param.type_name or "").lower()
            is_collection = tname.startswith(("list[", "sequence[")) or tname == "array"
            is_limit_name = param.name.lower() in _PAGINATION_OR_BATCH_NAMES
            if is_collection or is_limit_name:
                out.append(
                    Diagnostic(
                        rule_id="MCP004",
                        rule_slug="unbounded-collection-or-limit",
                        severity=Severity.INFO,
                        category=Category.SCHEMA,
                        message=(
                            f"Parameter '{param.name}' in tool '{tool.name}' has no upper bound "
                            "(`maximum` / `maxItems` / `Field(le=...)` / `min(...)`)."
                        ),
                        source_file=tool.source_file,
                        line=param.line or tool.line,
                        tool_name=tool.name,
                        remediation="Enforce a strict ceiling (e.g. `Field(default=50, le=200)` or `maxItems: 100`).",
                    )
                )
    return out


@register_rule(
    rule_id="MCP015",
    slug="undocumented-parameter",
    severity=Severity.INFO,
    category=Category.SCHEMA,
    summary="Two or more tool parameters have no description, so an agent must guess their meaning.",
    remediation=(
        "Describe each parameter in the docstring `Args:` section, the JSON Schema "
        "`description`, or the Pydantic `Field(description=...)`."
    ),
)
def check_undocumented_parameters(
    tools: Sequence[ToolSpec], config: ScanConfig
) -> list[Diagnostic]:
    """Report tools whose parameters carry no description.

    Only tools with **two or more** parameters are considered: with a single argument there is
    nothing to disambiguate, and firing there would be noise with no diagnostic value.

    One finding per tool lists every undocumented parameter, because a wide schema with eight
    bare parameters is one documentation problem, not eight.

    Args:
        tools: Statically extracted or live-audited tool definitions.
        config: Effective scan configuration. Unused: the threshold is fixed at "two or more
            parameters", which is a product decision rather than a tunable.

    Returns:
        At most one diagnostic per tool, anchored on the first undocumented parameter.
    """
    del config
    out: list[Diagnostic] = []

    for tool in tools:
        if len(tool.parameters) < 2:
            continue
        undocumented = [p for p in tool.parameters if not (p.description or "").strip()]
        if not undocumented:
            continue

        names = [f"'{p.name}'" for p in undocumented]
        shown = ", ".join(names[:5])
        if len(names) > 5:
            shown = f"{shown} and {len(names) - 5} more"
        total = len(tool.parameters)
        out.append(
            Diagnostic(
                rule_id="MCP015",
                rule_slug="undocumented-parameter",
                severity=Severity.INFO,
                category=Category.SCHEMA,
                message=(
                    f"{len(undocumented)} of {total} parameters in tool '{tool.name}' have no "
                    f"description: {shown}."
                ),
                source_file=tool.source_file,
                line=undocumented[0].line or tool.line,
                tool_name=tool.name,
                remediation=(
                    "Describe what each argument means and what values are expected, so the "
                    "model can choose them correctly. In a docstring use an `Args:` section; "
                    "in JSON Schema add `description` to each property."
                ),
            )
        )
    return out
