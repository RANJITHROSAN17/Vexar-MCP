"""TOML configuration loader supporting `vexar-mcp.toml` and `pyproject.toml`.

The legacy `.mcp-doctor.toml` filename and `[tool.mcp-doctor]` pyproject table are
still honoured so projects configured before the Vexar-MCP rename keep working;
new configuration files should use `vexar-mcp.toml` / `[tool.vexar-mcp]`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp_doctor.models import ScanConfig

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

# Preferred name first, then the pre-rename name for backward compatibility.
CONFIG_FILENAMES = ("vexar-mcp.toml", ".mcp-doctor.toml")
PYPROJECT_TABLES = ("vexar-mcp", "mcp-doctor")


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
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


def load_config(
    config_path: Path | None = None,
    start_dir: Path | None = None,
    *,
    max_tokens_override: int | None = None,
    extra_ignored_rules: set[str] | None = None,
) -> ScanConfig:
    """Resolve effective ScanConfig from TOML files and CLI overrides.

    Args:
        config_path: Explicit configuration file to read. When omitted the loader
            searches ``start_dir`` for ``vexar-mcp.toml``, then ``.mcp-doctor.toml``,
            then a ``[tool.vexar-mcp]`` / ``[tool.mcp-doctor]`` pyproject table.
        start_dir: Directory (or file) used as the root of the configuration search.
        max_tokens_override: CLI ``--max-tokens`` value, which wins over file values.
        extra_ignored_rules: Rule IDs supplied through ``--ignore`` and merged with
            any ``ignore = [...]`` entries from the configuration file.

    Returns:
        The effective :class:`ScanConfig` for the scan.
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
        dedicated = next(
            (search_root / name for name in CONFIG_FILENAMES if (search_root / name).is_file()),
            None,
        )
        pyproject = search_root / "pyproject.toml"
        if dedicated is not None:
            raw_cfg = _read_toml(dedicated)
        elif pyproject.is_file():
            raw_cfg = _select_pyproject_table(_read_toml(pyproject))

    max_tool_tokens = int(raw_cfg.get("max-tool-tokens", 250))
    if max_tokens_override is not None:
        max_tool_tokens = max_tokens_override

    max_server_tokens = int(raw_cfg.get("max-server-tokens", 4000))

    ignored = {str(r).strip().upper() for r in raw_cfg.get("ignore", []) if str(r).strip()}
    if extra_ignored_rules:
        ignored |= {r.strip().upper() for r in extra_ignored_rules if r.strip()}

    default_excludes = ScanConfig().exclude_globs
    custom_excludes = tuple(str(x) for x in raw_cfg.get("exclude", []) if str(x).strip())
    exclude_globs = custom_excludes if custom_excludes else default_excludes

    return ScanConfig(
        max_tool_tokens=max_tool_tokens,
        max_server_tokens=max_server_tokens,
        ignored_rules=frozenset(ignored),
        exclude_globs=exclude_globs,
    )
