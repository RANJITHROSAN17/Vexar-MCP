"""Policy presets: named, versioned enforcement profiles for Vexar-MCP scans.

A preset bundles *detection* settings (token budgets) with *enforcement* settings
(fail-on severity, minimum health score, ignore allowlist) so that a team can adopt
one reviewable standard instead of a pile of CLI flags.

Precedence, highest wins:

1. explicit CLI flags (``--fail-on``, ``--max-tokens``, ``--ignore``, ...)
2. the configuration file that applies to the scan target
3. files that file inherits through ``extends``, nearest ancestor first
4. the selected preset defaults

A configuration file may declare ``extends = "strict-prod"`` or ``extends =
"../org/vexar-mcp.toml"``. Layers are applied root-first, so the file nearest the
code wins for scalar settings, while ``ignore``, ``exclude`` and ``rule-packs``
accumulate down the chain - a team can add to the organisation standard, never
quietly drop part of it.

Governance invariant across the chain: protected categories **accumulate**. If any
ancestor selects a preset that protects security rules, a child file cannot switch to
a weaker preset and silence one - the protection survives, and the error names the
file that tried.

Governance invariant: a preset may declare ``protected_categories``. Rule IDs in
those categories cannot be silenced by ``ignore`` - neither from the CLI nor from a
configuration file - because "the security profile that can be quietly disabled" is
not a security profile. Attempting it raises :class:`PolicyViolation`, which the CLI
reports as a usage error (exit code 2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

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

#: Accepted ``fail-on`` values. ``never`` is handled separately because it maps to
#: ``None`` rather than to a :class:`Severity`.
FAIL_ON_VALUES = frozenset({"error", "warning", "info", "never"})

#: Settings a layer can contribute, in reporting order. A tuple so ``--print-config``
#: output is byte-identical across runs on the same inputs.
POLICY_KEYS = (
    "policy",
    "fail-on",
    "min-score",
    "max-tool-tokens",
    "max-server-tokens",
    "ignore",
    "exclude",
    "rule-packs",
)


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


@dataclass(frozen=True)
class PolicyLayer:
    """One contributor to an effective policy.

    A layer is a *parsed* configuration document, a built-in preset named by one, or the
    CLI overrides. Keeping parsing out of the merge is what makes precedence testable
    without touching the filesystem.

    Attributes:
        origin: Human-readable provenance shown by ``--print-config`` - a file path, a
            preset reference, or ``cli``.
        preset_name: Preset this layer selects, if any. Applied at this point in the
            chain, so a child may select a different preset than its parent.
        fail_on: Raw ``fail-on`` value (``error``/``warning``/``info``/``never``).
        min_score: Raw ``min-score`` floor.
        max_tool_tokens: Raw per-tool token budget.
        max_server_tokens: Raw server-wide token budget.
        ignore: Rule IDs this layer asks to silence.
        exclude: Globs this layer adds to the scan exclusion.
        rule_packs: Rule pack specs this layer adds.
    """

    origin: str
    preset_name: str | None = None
    fail_on: str | None = None
    min_score: int | None = None
    max_tool_tokens: int | None = None
    max_server_tokens: int | None = None
    ignore: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    rule_packs: tuple[str, ...] = ()

    def contributed(self) -> tuple[str, ...]:
        """Return the settings this layer actually supplies, in reporting order."""
        keys: list[str] = []
        if self.preset_name:
            keys.append("policy")
        if self.fail_on is not None:
            keys.append("fail-on")
        if self.min_score is not None:
            keys.append("min-score")
        if self.max_tool_tokens is not None:
            keys.append("max-tool-tokens")
        if self.max_server_tokens is not None:
            keys.append("max-server-tokens")
        if self.ignore:
            keys.append("ignore")
        if self.exclude:
            keys.append("exclude")
        if self.rule_packs:
            keys.append("rule-packs")
        return tuple(keys)


@dataclass(frozen=True)
class ResolvedPolicy:
    """The outcome of merging every layer, plus the audit trail that produced it.

    Attributes:
        preset: Effective preset, with :attr:`PolicyPreset.protected_categories`
            widened to the union of every preset named in the chain.
        fail_on: Effective ``fail-on`` threshold; ``None`` means report-only.
        min_score: Effective score floor.
        max_tool_tokens: Effective per-tool token budget.
        max_server_tokens: Effective server-wide token budget.
        ignored_rules: Effective ignore set, after the protected-rule check.
        exclude_globs: Effective exclusion globs.
        rule_packs: Effective rule pack specs, in load order.
        chain: ``(origin, contributed settings)`` per layer, root layer first.
        sources: ``(setting, origin)`` for every setting a layer supplied.
        protected_categories: Effective protected categories, sorted by value.
    """

    preset: PolicyPreset
    fail_on: Severity | None
    min_score: int
    max_tool_tokens: int
    max_server_tokens: int
    ignored_rules: frozenset[str]
    exclude_globs: tuple[str, ...]
    rule_packs: tuple[str, ...]
    chain: tuple[tuple[str, tuple[str, ...]], ...]
    sources: tuple[tuple[str, str], ...]
    protected_categories: tuple[Category, ...]


def _last_setting(field_name: str, layers: Sequence[PolicyLayer]) -> Any:
    """Return the last non-empty value of ``field_name`` across ``layers``."""
    value: Any = None
    for layer in layers:
        candidate = getattr(layer, field_name)
        if candidate is not None and candidate != () and candidate != "":
            value = candidate
    return value


def _origin_of(field_name: str, layers: Sequence[PolicyLayer]) -> str:
    """Return the origin of the layer that supplied the winning ``field_name`` value."""
    origin = ""
    for layer in layers:
        candidate = getattr(layer, field_name)
        if candidate is not None and candidate != () and candidate != "":
            origin = layer.origin
    return origin


def _accumulate(first: Sequence[Category], second: Sequence[Category]) -> tuple[Category, ...]:
    """Union two category sequences into a deterministically ordered tuple."""
    return tuple(sorted({*first, *second}, key=lambda category: category.value))


def resolve_policy_layers(
    layers: Sequence[PolicyLayer],
    *,
    catalog: dict[str, Category],
    cli: PolicyLayer | None = None,
    allow_unknown: bool = False,
    default_excludes: tuple[str, ...] = (),
) -> ResolvedPolicy:
    """Merge an inheritance chain into one effective policy, root layer first.

    Args:
        layers: Configuration layers from the most ancestral file to the one nearest the
            scan target. Each may select a preset and/or override settings.
        catalog: Every known rule ID mapped to its category.
        cli: Explicit command-line overrides, applied after every file layer.
        allow_unknown: Permit ignore entries for rule IDs not yet known, so rule packs
            can be loaded before the final validation pass.
        default_excludes: Exclusion globs used when no layer supplies ``exclude``.

    Returns:
        The effective :class:`ResolvedPolicy` and the chain that produced it.

    Raises:
        PolicyViolation: An unknown preset, an extends cycle, a protected rule in an
            ``ignore`` list, or an unknown rule ID.
        ValueError: An invalid ``fail-on`` value or an out-of-range ``min-score``, with
            the contributing file named in the message.
    """
    overrides = [*layers, cli] if cli is not None else list(layers)

    # Protection is monotonic across the whole chain, the CLI layer included: every preset
    # any layer names contributes its protected categories. Selecting a weaker preset
    # further down - in a child file or in `--policy` - therefore cannot re-open a protected
    # category. Narrowing protection means editing the reviewed configuration, which is the
    # only place the change is visible in review.
    selected = DEFAULT_POLICY
    protected: set[Category] = set()
    for layer in overrides:
        if not layer.preset_name:
            continue
        preset = resolve_policy(layer.preset_name)
        selected = preset.name
        protected.update(preset.protected_categories)

    effective = replace(
        POLICIES[selected],
        protected_categories=tuple(sorted(protected, key=lambda category: category.value)),
    )

    sources: list[tuple[str, str]] = [
        ("policy", _origin_of("preset_name", overrides) or "built-in")
    ]

    fail_on_raw = _last_setting("fail_on", overrides)
    if fail_on_raw is None:
        fail_on: Severity | None = effective.fail_on
    else:
        normalized = str(fail_on_raw).strip().lower()
        if normalized == "never":
            fail_on = None
        elif normalized in FAIL_ON_VALUES:
            fail_on = Severity(normalized)
        else:
            raise ValueError(
                f"{_origin_of('fail_on', overrides)}: invalid 'fail-on' value "
                f"'{fail_on_raw}'. Use one of: {', '.join(sorted(FAIL_ON_VALUES))}"
            )
        sources.append(("fail-on", _origin_of("fail_on", overrides)))

    min_score_raw = _last_setting("min_score", overrides)
    min_score = int(effective.min_score if min_score_raw is None else min_score_raw)
    if not 0 <= min_score <= 100:
        raise ValueError(
            f"{_origin_of('min_score', overrides)}: 'min-score' must be between 0 and 100, "
            f"got {min_score}"
        )
    if min_score_raw is not None:
        sources.append(("min-score", _origin_of("min_score", overrides)))

    max_tool_raw = _last_setting("max_tool_tokens", overrides)
    max_tool_tokens = int(effective.max_tool_tokens if max_tool_raw is None else max_tool_raw)
    if max_tool_raw is not None:
        sources.append(("max-tool-tokens", _origin_of("max_tool_tokens", overrides)))

    max_server_raw = _last_setting("max_server_tokens", overrides)
    max_server_tokens = int(
        effective.max_server_tokens if max_server_raw is None else max_server_raw
    )
    if max_server_raw is not None:
        sources.append(("max-server-tokens", _origin_of("max_server_tokens", overrides)))

    # `ignore` accumulates: dropping a parent's entry would be silent weakening, so a child
    # can only add. Reported per origin so the error names the file that tried to silence a
    # protected rule, even when an ancestor is what protected it.
    requested: dict[str, str] = {}
    ignore_origins: list[str] = []
    for layer in overrides:
        for rule in layer.ignore:
            key = rule.strip().upper()
            if not key:
                continue
            requested.setdefault(key, layer.origin)
            if layer.origin not in ignore_origins:
                ignore_origins.append(layer.origin)
    if ignore_origins:
        sources.append(("ignore", ", ".join(ignore_origins)))

    protected_ids = effective.protected_rule_ids(catalog)
    # Where the protection came from decides the message: "policy 'default' protects security
    # rules" is nonsense to a reader, because `default` protects nothing - it inherits.
    own = set(POLICIES[selected].protected_categories)
    inherited = [category for category in effective.protected_categories if category not in own]
    inherited_names = ", ".join(category.value for category in inherited)
    for rule_id, origin in requested.items():
        if rule_id in protected_ids:
            if inherited:
                scope = (
                    f"inherits protection of {inherited_names} rules from an extended policy "
                    f"(the effective preset '{selected}' does not protect them itself)"
                )
            else:
                scope = (
                    f"policy '{selected}' protects "
                    f"{', '.join(c.value for c in effective.protected_categories)} rules"
                )
            raise PolicyViolation(
                f"{origin}: {scope}, so this cannot be ignored: {rule_id}. Protection can only "
                f"be narrowed by editing the configuration that declares it. Demote or fix the "
                f"finding instead."
            )

    ignored = apply_policy(
        effective,
        catalog=catalog,
        ignored_rules=frozenset(requested),
        allow_unknown=allow_unknown,
    )

    custom_excludes: list[str] = []
    for layer in overrides:
        custom_excludes.extend(item for item in layer.exclude if item.strip())
    if custom_excludes:
        sources.append(("exclude", _origin_of("exclude", overrides)))
    exclude_globs = tuple(dict.fromkeys(custom_excludes)) if custom_excludes else default_excludes

    rule_packs: list[str] = []
    for layer in overrides:
        rule_packs.extend(pack for pack in layer.rule_packs if pack)
    if rule_packs:
        sources.append(("rule-packs", _origin_of("rule_packs", overrides)))

    return ResolvedPolicy(
        preset=effective,
        fail_on=fail_on,
        min_score=min_score,
        max_tool_tokens=max_tool_tokens,
        max_server_tokens=max_server_tokens,
        ignored_rules=ignored,
        exclude_globs=exclude_globs,
        rule_packs=tuple(dict.fromkeys(rule_packs)),
        chain=tuple((layer.origin, layer.contributed()) for layer in overrides),
        sources=tuple(sorted(sources, key=lambda pair: POLICY_KEYS.index(pair[0]))),
        protected_categories=effective.protected_categories,
    )
