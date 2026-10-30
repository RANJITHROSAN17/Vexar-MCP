"""Configuration loader for `vexar-mcp.toml`, `pyproject.toml`, and policy presets.

The legacy `.mcp-doctor.toml` filename and `[tool.mcp-doctor]` pyproject table are
still honoured so projects configured before the Vexar-MCP rename keep working;
new configuration files should use `vexar-mcp.toml` / `[tool.vexar-mcp]`.

Resolution order, highest precedence first:

1. explicit CLI flags (``--policy``, ``--fail-on``, ``--min-score``, ``--max-tokens``,
   ``--ignore``, ``--rule-pack``)
2. the configuration file's own keys
3. files it inherits through ``extends``, nearest ancestor first
4. the selected policy preset's defaults

``extends`` takes a built-in preset name or a path to another configuration file,
resolved relative to the file that declares it. Chains are applied root-first and may
nest (organisation -> team -> repository). Cycles and dangling targets are errors, not
silent fallbacks, and protected categories accumulate along the chain so a child file
cannot silence a rule an ancestor protected.

Configuration keys (all optional)::

    extends = "../org/vexar-mcp.toml"   # preset name or path to inherit from
    policy = "strict-prod"          # default, ci, strict-prod, enterprise-governance
    fail-on = "warning"            # error | warning | info | never
    min-score = 90                 # 0-100
    max-tool-tokens = 200
    max-server-tokens = 3000
    ignore = ["MCP004"]
    exclude = ["vendor", "generated"]
    rule-packs = ["./policies/acme-rules.toml"]
    baseline = ".vexar-baseline.json"

Invalid configuration fails loudly with :class:`ValueError` instead of silently falling
back to defaults, because a scan that quietly ignores a typo is worse than no scan.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp_doctor.models import ScanConfig
from mcp_doctor.policies import (
    POLICIES,
    PolicyLayer,
    PolicyViolation,
    resolve_policy_layers,
)
from mcp_doctor.rules import get_rule_catalog

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

# Preferred name first, then the pre-rename name for backward compatibility.
CONFIG_FILENAMES = ("vexar-mcp.toml", ".mcp-doctor.toml")
PYPROJECT_TABLES = ("vexar-mcp", "mcp-doctor")

_KNOWN_KEYS = frozenset(
    {
        "extends",
        "policy",
        "fail-on",
        "min-score",
        "max-tool-tokens",
        "max-server-tokens",
        "ignore",
        "exclude",
        "rule-packs",
        "baseline",
    }
)
#: How deep an ``extends`` chain may nest before the loader refuses to continue.
#: Real organisation chains are two or three levels; anything deeper is a mistake.
_MAX_EXTENDS_DEPTH = 16


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read configuration file {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _select_pyproject_table_named(doc: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Return the first recognised ``[tool.<name>]`` table and the name it used."""
    tool_tbl = doc.get("tool", {})
    if not isinstance(tool_tbl, dict):
        return {}, None
    for table in PYPROJECT_TABLES:
        candidate = tool_tbl.get(table)
        if isinstance(candidate, dict):
            return candidate, table
    return {}, None


def _select_pyproject_table(doc: dict[str, Any]) -> dict[str, Any]:
    """Return the first recognised ``[tool.<name>]`` table from a pyproject file."""
    return _select_pyproject_table_named(doc)[0]


def _search_dirs(start_dir: Path | None) -> list[Path]:
    """Return candidate directories, nearest first, walking up to the VCS root.

    Walking up (rather than only checking the scan target) matches how Ruff, ESLint and
    Prettier behave: one project-level configuration applies to every subdirectory scan.
    The walk stops at the first directory containing ``.git``, so a parent checkout can
    never leak configuration into an unrelated project.
    """
    current = (start_dir or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent

    dirs: list[Path] = []
    while True:
        dirs.append(current)
        if (current / ".git").exists() or current.parent == current:
            return dirs
        current = current.parent


def _config_in(directory: Path) -> Path | None:
    """Return the configuration file directly inside ``directory``, if any."""
    for name in CONFIG_FILENAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    pyproject = directory / "pyproject.toml"
    if pyproject.is_file() and _select_pyproject_table(_read_toml(pyproject)):
        return pyproject
    return None


def discover_config(start_dir: Path | None = None) -> Path | None:
    """Return the configuration file a scan would use, if any.

    Useful for ``--print-config`` and for CI debugging: a scan that quietly picks up an
    unexpected file is a support ticket waiting to happen.
    """
    for directory in _search_dirs(start_dir):
        found = _config_in(directory)
        if found is not None:
            return found
    return None


def _as_str_list(raw: Any, key: str) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list):
        raise ValueError(f"configuration key '{key}' must be a list of strings")
    return [str(item) for item in raw if str(item).strip()]


def _as_int(raw: Any, key: str) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"configuration key '{key}' must be an integer")
    return raw


def _document_and_table(path: Path) -> tuple[dict[str, Any], str | None]:
    """Read one configuration file and return its keys plus the table name it used."""
    document = _read_toml(path)
    if path.name == "pyproject.toml":
        return _select_pyproject_table_named(document)
    return document, None


def _origin(path: Path, table: str | None) -> str:
    """Render a layer origin the way ``--print-config`` reports it."""
    return f"{path} [tool.{table}]" if table else str(path)


def _layer_from_document(
    raw: dict[str, Any], origin: str, *, strict_keys: bool = True
) -> tuple[PolicyLayer, str | None]:
    """Validate one configuration document and turn it into a :class:`PolicyLayer`.

    Returns the layer and the raw ``extends`` target, if any. Every error names the file
    it came from, because an inheritance chain makes "which file said this" the first
    question anyone asks.
    """
    if strict_keys:
        unknown = sorted(set(raw) - _KNOWN_KEYS)
        if unknown:
            raise ValueError(
                f"{origin}: unknown configuration key(s): "
                + ", ".join(unknown)
                + ". Supported keys: "
                + ", ".join(sorted(_KNOWN_KEYS))
            )

    extends = raw.get("extends")
    if extends is not None and not isinstance(extends, str):
        raise ValueError(f"{origin}: 'extends' must be a preset name or a file path")
    policy = raw.get("policy")
    if policy is not None and not isinstance(policy, str):
        raise ValueError(f"{origin}: 'policy' must be a preset name")
    fail_on = raw.get("fail-on")
    if fail_on is not None and not isinstance(fail_on, str):
        raise ValueError(f"{origin}: configuration key 'fail-on' must be a string")

    try:
        layer = PolicyLayer(
            origin=origin,
            preset_name=policy,
            fail_on=fail_on,
            min_score=_as_int(raw.get("min-score"), "min-score"),
            max_tool_tokens=_as_int(raw.get("max-tool-tokens"), "max-tool-tokens"),
            max_server_tokens=_as_int(raw.get("max-server-tokens"), "max-server-tokens"),
            ignore=tuple(_as_str_list(raw.get("ignore"), "ignore")),
            exclude=tuple(_as_str_list(raw.get("exclude"), "exclude")),
            rule_packs=tuple(_as_str_list(raw.get("rule-packs"), "rule-packs")),
        )
    except ValueError as exc:
        raise ValueError(f"{origin}: {exc}") from None
    return layer, (extends.strip() if extends and extends.strip() else None)


def _match_preset(name: str) -> str | None:
    """Return the canonical preset name for ``name``, or ``None`` if it is not one."""
    candidate = name.strip().lower()
    return candidate if candidate in POLICIES else None


def _collect_layers(
    path: Path,
    seen: list[Path],
    *,
    depth: int = 0,
    strict_keys: bool = True,
) -> list[PolicyLayer]:
    """Resolve one configuration file and everything it inherits, root layer first.

    Args:
        path: Configuration file to read.
        seen: Files already on the current chain, used to detect cycles.
        depth: Current nesting depth.
        strict_keys: Reject unrecognised keys instead of ignoring them.

    Returns:
        Layers ordered from the most ancestral file to ``path`` itself.

    Raises:
        PolicyViolation: On a cycle, an over-deep chain, or an ``extends`` value that is
            neither a preset nor an existing file.
        ValueError: When the file is missing, unreadable, or contains invalid keys.
    """
    resolved = path.resolve()
    if resolved in seen:
        cycle = " -> ".join([*(str(item) for item in seen), str(resolved)])
        raise PolicyViolation(f"policy 'extends' cycle: {cycle}")
    if depth > _MAX_EXTENDS_DEPTH:
        raise PolicyViolation(
            f"policy 'extends' chain deeper than {_MAX_EXTENDS_DEPTH} levels at {resolved}"
        )
    if not resolved.is_file():
        raise ValueError(f"configuration file not found: {path}")

    raw, table = _document_and_table(resolved)
    if resolved.name == "pyproject.toml" and table is None:
        raise ValueError(f"{resolved}: no [tool.{PYPROJECT_TABLES[0]}] table to inherit from")
    origin = _origin(resolved, table)
    layer, extends = _layer_from_document(raw, origin, strict_keys=strict_keys)

    layers: list[PolicyLayer] = []
    if extends:
        preset = _match_preset(extends)
        if preset is not None:
            # A preset is the root of a chain: it contributes defaults and, importantly, its
            # protected categories, which the rest of the chain can only widen.
            layers.append(PolicyLayer(origin=f"{origin} extends '{preset}'", preset_name=preset))
        else:
            target = (resolved.parent / extends).resolve()
            if not target.is_file():
                known = ", ".join(sorted(POLICIES))
                raise PolicyViolation(
                    f"{origin}: extends = '{extends}' is neither a built-in preset "
                    f"({known}) nor a configuration file ({target})"
                )
            layers.extend(
                _collect_layers(
                    target,
                    [*seen, resolved],
                    depth=depth + 1,
                    strict_keys=strict_keys,
                )
            )
    layers.append(layer)
    return layers


def load_config(
    config_path: Path | None = None,
    start_dir: Path | None = None,
    *,
    max_tokens_override: int | None = None,
    extra_ignored_rules: set[str] | None = None,
    policy_override: str | None = None,
    fail_on_override: str | None = None,
    min_score_override: int | None = None,
    extra_rule_packs: list[str] | None = None,
    strict_keys: bool = True,
    allow_unknown_rules: bool = False,
) -> ScanConfig:
    """Resolve the effective ScanConfig from TOML files, presets, and CLI overrides.

    Args:
        config_path: Explicit configuration file to read. When omitted the loader
            searches ``start_dir`` for ``vexar-mcp.toml``, then ``.mcp-doctor.toml``,
            then a ``[tool.vexar-mcp]`` / ``[tool.mcp-doctor]`` pyproject table.
        start_dir: Directory (or file) used as the root of the configuration search.
        max_tokens_override: CLI ``--max-tokens`` value, which wins over file and preset.
        extra_ignored_rules: Rule IDs supplied through ``--ignore`` and merged with
            any ``ignore = [...]`` entries from the configuration file.
        policy_override: CLI ``--policy`` preset name.
        fail_on_override: CLI ``--fail-on`` threshold.
        min_score_override: CLI ``--min-score`` floor.
        extra_rule_packs: CLI ``--rule-pack`` specs, appended to configured packs.
        strict_keys: Reject unrecognised configuration keys instead of ignoring them.
        allow_unknown_rules: Permit ignore entries for rule IDs not yet in the catalog,
            so rule packs can be loaded before the final validation pass.

    Returns:
        The effective :class:`ScanConfig` for the scan.

    Raises:
        ValueError: On unreadable files, unknown keys, invalid values, or a policy
            violation such as ignoring a protected security rule.
    """
    search_root = (start_dir or Path.cwd()).resolve()
    if search_root.is_file():
        search_root = search_root.parent

    primary = config_path if config_path is not None else discover_config(search_root)
    layers: list[PolicyLayer] = (
        _collect_layers(primary, [], strict_keys=strict_keys) if primary is not None else []
    )

    cli = PolicyLayer(
        origin="cli",
        preset_name=policy_override,
        fail_on=fail_on_override,
        min_score=min_score_override,
        max_tool_tokens=max_tokens_override,
        ignore=tuple(sorted(rule.strip() for rule in extra_ignored_rules or () if rule.strip())),
        rule_packs=tuple(pack for pack in extra_rule_packs or () if pack),
    )

    resolved = resolve_policy_layers(
        layers,
        catalog=get_rule_catalog(),
        cli=cli if cli.contributed() else None,
        allow_unknown=allow_unknown_rules,
        default_excludes=ScanConfig().exclude_globs,
    )

    return ScanConfig(
        max_tool_tokens=resolved.max_tool_tokens,
        max_server_tokens=resolved.max_server_tokens,
        ignored_rules=resolved.ignored_rules,
        policy_name=resolved.preset.name,
        fail_on=resolved.fail_on,
        min_score=resolved.min_score,
        rule_packs=resolved.rule_packs,
        exclude_globs=resolved.exclude_globs,
        policy_chain=resolved.chain,
        policy_sources=resolved.sources,
        protected_categories=tuple(category.value for category in resolved.protected_categories),
    )


def describe_config(config: ScanConfig) -> dict[str, Any]:
    """Return a JSON-serializable view of the effective configuration.

    The flat keys are the effective values. ``resolution`` explains them: the chain of
    layers from the most ancestral file to the CLI, which settings each one contributed,
    and which layer won for every effective setting.
    """
    return {
        "policy": config.policy_name,
        "fail_on": config.fail_on.value if config.fail_on is not None else "never",
        "min_score": config.min_score,
        "max_tool_tokens": config.max_tool_tokens,
        "max_server_tokens": config.max_server_tokens,
        "ignore": sorted(config.ignored_rules),
        "exclude": list(config.exclude_globs),
        "rule_packs": list(config.rule_packs),
        "protected_categories": list(config.protected_categories),
        "resolution": {
            "chain": [
                {"origin": origin, "contributed": list(contributed)}
                for origin, contributed in config.policy_chain
            ],
            "sources": dict(config.policy_sources),
        },
    }
