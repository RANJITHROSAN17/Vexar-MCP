"""Policy presets: named, versioned enforcement profiles for Vexar-MCP scans.

A preset bundles *detection* settings (token budgets) with *enforcement* settings
(fail-on severity, minimum health score, ignore allowlist) so that a team can adopt
one reviewable standard instead of a pile of CLI flags.

Precedence, highest wins:

1. explicit CLI flags (``--fail-on``, ``--max-tokens``, ``--ignore``, ...)
2. ``[policy]`` table in ``vexar-mcp.toml`` / ``pyproject.toml``
3. the selected preset defaults

Governance invariant: a preset may declare ``protected_categories``. Rule IDs in
those categories cannot be silenced by ``ignore`` - neither from the CLI nor from a
configuration file - because "the security profile that can be quietly disabled" is
not a security profile. Attempting it raises :class:`PolicyViolation`, which the CLI
reports as a usage error (exit code 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mcp_doctor.models import Category, Severity


class PolicyViolation(ValueError):
    """Raised when configuration attempts to weaken a protected policy guarantee."""


@dataclass(frozen=True)
class PolicyPreset:
    """A named enforcement profile.

    Attributes:
        name: Stable identifier used by ``--policy`` and config files.
        title: Human-readable one-line title.
        description: What the preset is for and why its defaults are set that way.
        fail_on: Minimum severity that fails the run.
        min_score: Minimum acceptable health score (0-100).
        max_tool_tokens: Per-tool token budget.
        max_server_tokens: Server-wide token budget.
        protected_categories: Categories whose rules can never be ignored.
        default_ignored_rules: Rules silenced by default under this preset.
    """

    name: str
    title: str
    description: str
    fail_on: Severity
    min_score: int
    max_tool_tokens: int
    max_server_tokens: int
    protected_categories: tuple[Category, ...] = ()
    default_ignored_rules: frozenset[str] = field(default_factory=frozenset)

    def protected_rule_ids(self, catalog: dict[str, Category]) -> frozenset[str]:
        """Resolve protected categories to concrete rule IDs using the live rule catalog."""
        return frozenset(
            rule_id
            for rule_id, category in catalog.items()
            if category in self.protected_categories
        )


_PRESETS: tuple[PolicyPreset, ...] = (
    PolicyPreset(
        name="default",
        title="Default (developer laptop)",
        description=(
            "Low-friction profile for local exploration. Fails only on errors, enforces no "
            "score floor and silences nothing, so scans stay honest by default."
        ),
        fail_on=Severity.ERROR,
        min_score=0,
        max_tool_tokens=250,
        max_server_tokens=4000,
    ),
    PolicyPreset(
        name="ci",
        title="CI gate (recommended for pull requests)",
        description=(
            "Balanced merge gate: warnings fail the build and the health score must stay at "
            "B (80) or better. Pair with --baseline to gate only on newly introduced findings."
        ),
        fail_on=Severity.WARNING,
        min_score=80,
        max_tool_tokens=230,
        max_server_tokens=3800,
        protected_categories=(Category.SECURITY,),
    ),
    PolicyPreset(
        name="strict-prod",
        title="Strict production",
        description=(
            "Release gate for MCP servers exposed to real users or agent swarms. Warnings fail, "
            "score floor is 90 (A), token budgets are tight, and every security rule is "
            "unignorable."
        ),
        fail_on=Severity.WARNING,
        min_score=90,
        max_tool_tokens=200,
        max_server_tokens=3000,
        protected_categories=(Category.SECURITY,),
    ),
    PolicyPreset(
        name="enterprise-governance",
        title="Enterprise governance",
        description=(
            "Audit profile for regulated or multi-team estates. Zero tolerance downstream: even "
            "informational findings and token-budget drift fail the run, the score floor is A+ "
            "(97), and both security and contract rules are unignorable so LLM routing quality "
            "cannot degrade silently."
        ),
        fail_on=Severity.INFO,
        min_score=97,
        max_tool_tokens=180,
        max_server_tokens=2500,
        protected_categories=(Category.SECURITY, Category.CONTRACT),
    ),
)

POLICIES: dict[str, PolicyPreset] = {preset.name: preset for preset in _PRESETS}
DEFAULT_POLICY = "default"


def list_policies() -> list[PolicyPreset]:
    """Return all built-in presets in declaration order."""
    return list(_PRESETS)


def resolve_policy(name: str | None) -> PolicyPreset:
    """Look up a preset by name, raising :class:`PolicyViolation` when unknown."""
    key = (name or DEFAULT_POLICY).strip().lower()
    preset = POLICIES.get(key)
    if preset is None:
        known = ", ".join(sorted(POLICIES))
        raise PolicyViolation(f"unknown policy preset '{name}'. Available presets: {known}")
    return preset


def apply_policy(
    preset: PolicyPreset,
    *,
    catalog: dict[str, Category],
    ignored_rules: frozenset[str] = frozenset(),
    allow_unknown: bool = False,
) -> frozenset[str]:
    """Merge requested ignores with a preset, enforcing protected categories.

    Args:
        preset: The selected enforcement profile.
        catalog: Mapping of every known rule ID to its category.
        ignored_rules: Rule IDs requested through CLI flags or configuration files.
        allow_unknown: Skip the unknown-rule check. Used during the first configuration
            pass, when rule packs have not been imported yet and their IDs are not in
            the catalog. The CLI re-validates after packs load.

    Returns:
        The effective set of ignored rule IDs.

    Raises:
        PolicyViolation: If an ignore request targets a protected category, or names a
            rule ID that does not exist in the active catalog.
    """
    requested = {rule_id.strip().upper() for rule_id in ignored_rules if rule_id.strip()}

    unknown = sorted(rule_id for rule_id in requested if rule_id not in catalog)
    if unknown and not allow_unknown:
        raise PolicyViolation(
            "unknown rule ID(s) in ignore list: "
            + ", ".join(unknown)
            + ". Run `vexar-mcp rules` to list valid IDs."
        )

    protected = preset.protected_rule_ids(catalog)
    violations = sorted(requested & protected)
    if violations:
        raise PolicyViolation(
            f"policy '{preset.name}' protects {', '.join(c.value for c in preset.protected_categories)} "
            f"rules; these cannot be ignored: {', '.join(violations)}. "
            "Demote or fix the finding instead, or choose a weaker preset."
        )

    return frozenset(requested | set(preset.default_ignored_rules))
