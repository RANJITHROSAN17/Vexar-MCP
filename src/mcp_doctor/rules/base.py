"""Pluggable rule registry for mcp-doctor."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from mcp_doctor.models import Category, Diagnostic, ScanConfig, Severity, ToolSpec

RuleCheckFn = Callable[[Sequence[ToolSpec], ScanConfig], list[Diagnostic]]


@dataclass(frozen=True)
class RuleDefinition:
    """Metadata and execution callback for a registered lint rule."""

    rule_id: str
    slug: str
    severity: Severity
    category: Category
    summary: str
    remediation: str
    check: RuleCheckFn


_REGISTRY: dict[str, RuleDefinition] = {}


def register_rule(
    *,
    rule_id: str,
    slug: str,
    severity: Severity,
    category: Category,
    summary: str,
    remediation: str,
) -> Callable[[RuleCheckFn], RuleCheckFn]:
    """Decorator registering a lint rule in the global rule table."""

    def _decorator(fn: RuleCheckFn) -> RuleCheckFn:
        # Route through register_definition so the decorator shares one guard with
        # programmatic registration: duplicate IDs and duplicate slugs raise instead of
        # silently replacing an existing rule.
        register_definition(
            RuleDefinition(
                rule_id=rule_id,
                slug=slug,
                severity=severity,
                category=category,
                summary=summary,
                remediation=remediation,
                check=fn,
            )
        )
        return fn

    return _decorator


def get_all_rules() -> list[RuleDefinition]:
    """Return all registered rules ordered by rule ID."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def snapshot_registry() -> dict[str, RuleDefinition]:
    """Return a copy of the current registry.

    Rule packs register into a process-global table, which is what a CLI wants (load
    once, scan once) but is surprising for long-lived library hosts and confusing in
    tests. Snapshot/restore lets callers scope pack loading to a single unit of work.
    """
    return dict(_REGISTRY)


def restore_registry(snapshot: dict[str, RuleDefinition]) -> None:
    """Restore a previously captured registry snapshot."""
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


def get_rule_catalog() -> dict[str, Category]:
    """Return a mapping of every registered rule ID to its category."""
    return {rule_id: definition.category for rule_id, definition in _REGISTRY.items()}


def register_definition(definition: RuleDefinition) -> None:
    """Register an externally built rule definition, rejecting ID collisions.

    Third-party rule packs must not be able to shadow a built-in rule: silently
    replacing ``MCP005`` with a no-op would turn the scanner into a rubber stamp.
    Colliding IDs therefore raise instead of overwriting.

    Raises:
        ValueError: When the rule ID is already registered or its slug is in use.
    """
    if definition.rule_id in _REGISTRY:
        raise ValueError(
            f"rule ID '{definition.rule_id}' is already registered and cannot be overridden"
        )
    if any(existing.slug == definition.slug for existing in _REGISTRY.values()):
        raise ValueError(f"rule slug '{definition.slug}' is already registered")
    _REGISTRY[definition.rule_id] = definition
