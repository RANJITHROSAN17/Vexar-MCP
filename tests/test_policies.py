"""Tests for policy presets and the protected-rule governance invariant."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_doctor.config import describe_config, discover_config, load_config
from mcp_doctor.models import Category, Severity
from mcp_doctor.policies import (
    DEFAULT_POLICY,
    POLICIES,
    PolicyViolation,
    apply_policy,
    list_policies,
    resolve_policy,
)
from mcp_doctor.rules import get_rule_catalog


def test_builtin_presets_are_stable() -> None:
    names = [p.name for p in list_policies()]
    assert names == ["default", "ci", "strict-prod", "enterprise-governance"]
    assert DEFAULT_POLICY == "default"


def test_preset_ordering_is_strictly_increasing() -> None:
    default = POLICIES["default"]
    ci = POLICIES["ci"]
    strict = POLICIES["strict-prod"]
    enterprise = POLICIES["enterprise-governance"]

    # Stricter presets fail on *lower* severities, so rank decreases as the policy tightens.
    assert default.fail_on.rank > ci.fail_on.rank >= strict.fail_on.rank >= enterprise.fail_on.rank
    assert default.min_score < ci.min_score < strict.min_score < enterprise.min_score
    assert (
        default.max_tool_tokens
        > ci.max_tool_tokens
        > strict.max_tool_tokens
        > enterprise.max_tool_tokens
    )


def test_resolve_policy_is_case_insensitive_and_validates() -> None:
    assert resolve_policy("STRICT-PROD").name == "strict-prod"
    assert resolve_policy(None).name == DEFAULT_POLICY
    with pytest.raises(PolicyViolation, match="unknown policy preset"):
        resolve_policy("lenient")


def test_protected_rule_ids_resolve_from_catalog() -> None:
    catalog = get_rule_catalog()
    protected = POLICIES["strict-prod"].protected_rule_ids(catalog)
    assert {"MCP005", "MCP006", "MCP007", "MCP009", "MCP010", "MCP011", "MCP013"} <= protected
    assert "MCP001" not in protected


def test_apply_policy_rejects_ignoring_protected_rules() -> None:
    with pytest.raises(PolicyViolation, match="cannot be ignored"):
        apply_policy(
            POLICIES["strict-prod"],
            catalog=get_rule_catalog(),
            ignored_rules=frozenset({"MCP005"}),
        )


def test_apply_policy_allows_ignoring_unprotected_rules() -> None:
    result = apply_policy(
        POLICIES["strict-prod"], catalog=get_rule_catalog(), ignored_rules=frozenset({"MCP004"})
    )
    assert result == frozenset({"MCP004"})


def test_apply_policy_rejects_unknown_rule_ids() -> None:
    with pytest.raises(PolicyViolation, match="unknown rule ID"):
        apply_policy(
            POLICIES["default"], catalog=get_rule_catalog(), ignored_rules=frozenset({"MCP999"})
        )


def test_apply_policy_can_skip_unknown_ids_for_pack_loading() -> None:
    result = apply_policy(
        POLICIES["default"],
        catalog=get_rule_catalog(),
        ignored_rules=frozenset({"ACME001"}),
        allow_unknown=True,
    )
    assert result == frozenset({"ACME001"})


def test_default_policy_protects_nothing_so_local_work_is_not_blocked() -> None:
    assert POLICIES["default"].protected_categories == ()


def test_config_uses_policy_defaults(tmp_path: Path) -> None:
    (tmp_path / "vexar-mcp.toml").write_text('policy = "strict-prod"\n', encoding="utf-8")
    cfg = load_config(start_dir=tmp_path)
    assert cfg.policy_name == "strict-prod"
    assert cfg.fail_on is Severity.WARNING
    assert cfg.min_score == 90
    assert cfg.max_tool_tokens == 200


def test_cli_overrides_beat_config_file(tmp_path: Path) -> None:
    (tmp_path / "vexar-mcp.toml").write_text(
        'policy = "strict-prod"\nmin-score = 95\nmax-tool-tokens = 150\n', encoding="utf-8"
    )
    cfg = load_config(
        start_dir=tmp_path,
        policy_override="ci",
        min_score_override=50,
        max_tokens_override=300,
    )
    assert cfg.policy_name == "ci"
    assert cfg.min_score == 50
    assert cfg.max_tool_tokens == 300


def test_config_reports_policy_violation_for_protected_ignores(tmp_path: Path) -> None:
    (tmp_path / "vexar-mcp.toml").write_text(
        'policy = "strict-prod"\nignore = ["MCP005"]\n', encoding="utf-8"
    )
    with pytest.raises(PolicyViolation, match="cannot be ignored"):
        load_config(start_dir=tmp_path)


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    (tmp_path / "vexar-mcp.toml").write_text("max_tokens = 10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown configuration key"):
        load_config(start_dir=tmp_path)


def test_config_rejects_bad_values(tmp_path: Path) -> None:
    (tmp_path / "vexar-mcp.toml").write_text('fail-on = "sometimes"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid 'fail-on'"):
        load_config(start_dir=tmp_path)

    (tmp_path / "vexar-mcp.toml").write_text("min-score = 400\n", encoding="utf-8")
    with pytest.raises(ValueError, match="between 0 and 100"):
        load_config(start_dir=tmp_path)

    (tmp_path / "vexar-mcp.toml").write_text("max-tool-tokens = 'many'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an integer"):
        load_config(start_dir=tmp_path)


def test_fail_on_never_maps_to_none(tmp_path: Path) -> None:
    (tmp_path / "vexar-mcp.toml").write_text('fail-on = "never"\n', encoding="utf-8")
    cfg = load_config(start_dir=tmp_path)
    assert cfg.fail_on is None
    assert describe_config(cfg)["fail_on"] == "never"


def test_rule_packs_are_read_from_config(tmp_path: Path) -> None:
    (tmp_path / "vexar-mcp.toml").write_text(
        'rule-packs = ["./policies/acme.toml"]\n', encoding="utf-8"
    )
    cfg = load_config(start_dir=tmp_path, extra_rule_packs=["./extra.toml"])
    assert cfg.rule_packs == ("./policies/acme.toml", "./extra.toml")


def test_enterprise_preset_protects_contract_and_security() -> None:
    preset = resolve_policy("enterprise-governance")
    assert preset.protected_categories == (Category.SECURITY, Category.CONTRACT)
    with pytest.raises(PolicyViolation):
        apply_policy(preset, catalog=get_rule_catalog(), ignored_rules=frozenset({"MCP001"}))


def test_config_is_discovered_from_a_parent_directory(tmp_path: Path) -> None:
    """A project-level config applies when scanning a nested subdirectory."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "vexar-mcp.toml").write_text('policy = "ci"\n', encoding="utf-8")
    nested = tmp_path / "src" / "servers"
    nested.mkdir(parents=True)
    assert discover_config(nested) == tmp_path / "vexar-mcp.toml"
    assert load_config(start_dir=nested).policy_name == "ci"


def test_config_search_stops_at_the_vcs_root(tmp_path: Path) -> None:
    """A parent checkout's configuration must never leak into a nested repository."""
    (tmp_path / "vexar-mcp.toml").write_text('policy = "strict-prod"\n', encoding="utf-8")
    inner = tmp_path / "inner"
    (inner / ".git").mkdir(parents=True)
    assert discover_config(inner) is None
    assert load_config(start_dir=inner).policy_name == "default"


def test_pyproject_table_is_discovered(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n\n[tool.vexar-mcp]\npolicy = "ci"\n', encoding="utf-8"
    )
    assert discover_config(tmp_path) == tmp_path / "pyproject.toml"
    assert load_config(start_dir=tmp_path).policy_name == "ci"
