"""Configuration loader for `vexar-mcp.toml`, `pyproject.toml`, and policy presets.

The legacy `.mcp-doctor.toml` filename and `[tool.mcp-doctor]` pyproject table are
still honoured so projects configured before the Vexar-MCP rename keep working;
new configuration files should use `vexar-mcp.toml` / `[tool.vexar-mcp]`.

Resolution order, highest precedence first:

1. explicit CLI flags (``--policy``, ``--fail-on``, ``--min-score``, ``--max-tokens``,
   ``--ignore``, ``--rule-pack``)
2. the configuration file's own keys
3. the selected policy preset's defaults

Configuration keys (all optional)::

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

from mcp_doctor.models import ScanConfig, Severity
from mcp_doctor.policies import PolicyPreset, apply_policy, resolve_policy
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
_VALID_FAIL_ON = frozenset({"error", "warning", "info", "never"})


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read configuration file {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _select_pyproject_table(doc: dict[str, Any]) -> dict[str, Any]:
    """Return the first recognised ``[tool.<name>]`` table from a pyproject file."""
    tool_tbl = doc.get("tool", {})
    if not isinstance(tool_tbl, dict):
        return {}
    for table in PYPROJECT_TABLES:
        candidate = tool_tbl.get(table)
        if isinstance(candidate, dict):
            return candidate
    return {}


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
    raw_cfg: dict[str, Any] = {}
    search_root = (start_dir or Path.cwd()).resolve()
    if search_root.is_file():
        search_root = search_root.parent

    if config_path is not None:
        raw_cfg = _read_toml(config_path)
        if "tool" in raw_cfg and isinstance(raw_cfg["tool"], dict):
            raw_cfg = _select_pyproject_table(raw_cfg) or raw_cfg
    else:
        found = discover_config(search_root)
        if found is not None:
            document = _read_toml(found)
            raw_cfg = (
                _select_pyproject_table(document) if found.name == "pyproject.toml" else document
            )

    if strict_keys:
        unknown = sorted(set(raw_cfg) - _KNOWN_KEYS)
        if unknown:
            raise ValueError(
                "unknown configuration key(s): "
                + ", ".join(unknown)
                + ". Supported keys: "
                + ", ".join(sorted(_KNOWN_KEYS))
            )

    preset: PolicyPreset = resolve_policy(policy_override or raw_cfg.get("policy"))

    max_tool_tokens = _as_int(raw_cfg.get("max-tool-tokens"), "max-tool-tokens")
    if max_tool_tokens is None:
        max_tool_tokens = preset.max_tool_tokens
    if max_tokens_override is not None:
        max_tool_tokens = max_tokens_override

    max_server_tokens = _as_int(raw_cfg.get("max-server-tokens"), "max-server-tokens")
    if max_server_tokens is None:
        max_server_tokens = preset.max_server_tokens

    min_score = _as_int(raw_cfg.get("min-score"), "min-score")
    if min_score is None:
        min_score = preset.min_score
    if min_score_override is not None:
        min_score = min_score_override
    if not 0 <= min_score <= 100:
        raise ValueError(f"'min-score' must be between 0 and 100, got {min_score}")

    fail_on_raw = fail_on_override or str(raw_cfg.get("fail-on", preset.fail_on.value)).lower()
    if fail_on_raw == "never":
        fail_on = None
    elif fail_on_raw in _VALID_FAIL_ON:
        fail_on = Severity(fail_on_raw)
    else:
        raise ValueError(
            f"invalid 'fail-on' value '{fail_on_raw}'. "
            f"Use one of: {', '.join(sorted(_VALID_FAIL_ON))}"
        )

    requested_ignores = {
        rule.strip().upper() for rule in _as_str_list(raw_cfg.get("ignore"), "ignore")
    }
    if extra_ignored_rules:
        requested_ignores |= {r.strip().upper() for r in extra_ignored_rules if r.strip()}
    ignored = apply_policy(
        preset,
        catalog=get_rule_catalog(),
        ignored_rules=frozenset(requested_ignores),
        allow_unknown=allow_unknown_rules,
    )

    default_excludes = ScanConfig().exclude_globs
    custom_excludes = tuple(x for x in _as_str_list(raw_cfg.get("exclude"), "exclude") if x.strip())
    exclude_globs = custom_excludes if custom_excludes else default_excludes

    rule_packs = list(_as_str_list(raw_cfg.get("rule-packs"), "rule-packs"))
    if extra_rule_packs:
        rule_packs.extend(pack for pack in extra_rule_packs if pack)

    return ScanConfig(
        max_tool_tokens=max_tool_tokens,
        max_server_tokens=max_server_tokens,
        ignored_rules=ignored,
        policy_name=preset.name,
        fail_on=fail_on,
        min_score=min_score,
        rule_packs=tuple(dict.fromkeys(rule_packs)),
        exclude_globs=exclude_globs,
    )


def describe_config(config: ScanConfig) -> dict[str, Any]:
    """Return a JSON-serializable view of the effective configuration."""
    return {
        "policy": config.policy_name,
        "fail_on": config.fail_on.value if config.fail_on is not None else "never",
        "min_score": config.min_score,
        "max_tool_tokens": config.max_tool_tokens,
        "max_server_tokens": config.max_server_tokens,
        "ignore": sorted(config.ignored_rules),
        "exclude": list(config.exclude_globs),
        "rule_packs": list(config.rule_packs),
    }
