"""Tests for policy inheritance: ``extends``, deterministic precedence, and the audit trail.

The feature exists so an organisation standard can be one file with narrow, reviewable team
exceptions. That makes two properties load-bearing, and both are tested here rather than
asserted in prose:

* **precedence is deterministic** - with three conflicting layers, exactly one wins for each
  setting, and reordering the same layers in a different way never changes the result;
* **protection is monotonic** - a child file that selects a weaker preset still inherits the
  protected categories of its ancestors, so "just extend the org file and switch to
  `default`" is not a way to silence a security rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_doctor.cli import main
from mcp_doctor.config import describe_config, load_config
from mcp_doctor.models import Category, Severity
from mcp_doctor.policies import (
    PolicyLayer,
    PolicyViolation,
    resolve_policy_layers,
)
from mcp_doctor.rules import get_rule_catalog

CATALOG = get_rule_catalog()


def write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def org_tree(tmp_path: Path) -> Path:
    """An organisation standard, a team layer, and a repository layer on top of both."""
    (tmp_path / ".git").mkdir()
    write(
        tmp_path / "standards" / "org.toml",
        'policy = "strict-prod"\n'
        "max-tool-tokens = 190\n"
        "max-server-tokens = 2800\n"
        'ignore = ["MCP004"]\n'
        'exclude = ["vendor"]\n',
    )
    write(
        tmp_path / "teams" / "payments.toml",
        'extends = "../standards/org.toml"\n'
        "min-score = 95\n"
        'ignore = ["MCP008"]\n'
        'exclude = ["generated"]\n',
    )
    return tmp_path


class TestExtendsPreset:
    def test_extends_a_builtin_preset_and_overrides_one_setting(self, tmp_path: Path) -> None:
        """The headline case: ``extends = "strict-prod"`` plus a narrow exception."""
        (tmp_path / ".git").mkdir()
        write(
            tmp_path / "vexar-mcp.toml",
            'extends = "strict-prod"\nmax-tool-tokens = 175\n',
        )
        cfg = load_config(start_dir=tmp_path)
        assert cfg.policy_name == "strict-prod"
        # Inherited from the preset the file extends.
        assert cfg.min_score == 90
        assert cfg.fail_on is Severity.WARNING
        # Overridden by the file itself.
        assert cfg.max_tool_tokens == 175

    def test_extends_preset_name_is_case_insensitive(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "vexar-mcp.toml", 'extends = "STRICT-PROD"\n')
        cfg = load_config(start_dir=tmp_path)
        assert cfg.policy_name == "strict-prod"
        assert cfg.min_score == 90

    def test_file_may_override_the_preset_it_extends(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(
            tmp_path / "vexar-mcp.toml",
            'extends = "enterprise-governance"\npolicy = "ci"\n',
        )
        cfg = load_config(start_dir=tmp_path)
        assert cfg.policy_name == "ci"
        # Protection from the *extended* preset is not lost by naming a weaker one.
        assert "security" in cfg.protected_categories
        assert "contract" in cfg.protected_categories


class TestPrecedence:
    def test_nearest_layer_wins_for_each_scalar(self, org_tree: Path) -> None:
        write(org_tree / "vexar-mcp.toml", 'extends = "teams/payments.toml"\nmin-score = 92\n')
        cfg = load_config(start_dir=org_tree)

        assert cfg.min_score == 92  # repo layer, not the team's 95
        assert cfg.max_tool_tokens == 190  # from the org layer, untouched downstream
        assert cfg.max_server_tokens == 2800
        assert cfg.policy_name == "strict-prod"  # only the org layer names a preset
        assert cfg.fail_on is Severity.WARNING

    def test_list_settings_accumulate_down_the_chain(self, org_tree: Path) -> None:
        write(org_tree / "vexar-mcp.toml", 'extends = "teams/payments.toml"\n')
        cfg = load_config(start_dir=org_tree)

        # Both layers' ignores survive: a child can add to the standard, not remove from it.
        assert sorted(cfg.ignored_rules) == ["MCP004", "MCP008"]
        assert cfg.exclude_globs == ("vendor", "generated")

    def test_rule_packs_accumulate_in_load_order_without_duplicates(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "org.toml", 'rule-packs = ["./packs/org.toml"]\n')
        write(
            tmp_path / "team.toml",
            'extends = "org.toml"\nrule-packs = ["./packs/team.toml", "./packs/org.toml"]\n',
        )
        write(tmp_path / "vexar-mcp.toml", 'extends = "team.toml"\n')
        cfg = load_config(start_dir=tmp_path)
        assert cfg.rule_packs == ("./packs/org.toml", "./packs/team.toml")

    def test_conflicting_values_resolve_to_the_nearest_layer(self, tmp_path: Path) -> None:
        """Three layers set the same key; only the repository layer's value survives."""
        (tmp_path / ".git").mkdir()
        write(tmp_path / "a.toml", 'policy = "ci"\nmin-score = 70\n')
        write(tmp_path / "b.toml", 'extends = "a.toml"\nmin-score = 80\n')
        write(tmp_path / "vexar-mcp.toml", 'extends = "b.toml"\nmin-score = 85\n')
        assert load_config(start_dir=tmp_path).min_score == 85

        # Dropping the most specific layer falls back deterministically to the next one.
        (tmp_path / "vexar-mcp.toml").unlink()
        assert load_config(config_path=tmp_path / "b.toml").min_score == 80

    def test_same_layers_in_chain_order_are_order_independent(self) -> None:
        """Merging is a function of chain order, not of dict iteration or file mtimes."""
        layers = [
            PolicyLayer(origin="org.toml", preset_name="strict-prod", min_score=91),
            PolicyLayer(origin="team.toml", max_tool_tokens=180),
            PolicyLayer(origin="repo.toml", min_score=95),
        ]
        first = resolve_policy_layers(layers, catalog=CATALOG)
        second = resolve_policy_layers(list(reversed(layers)), catalog=CATALOG)
        assert first.min_score == 95
        assert second.min_score == 91  # reversing the chain reverses precedence, knowingly

    def test_cli_overrides_every_layer(self, org_tree: Path) -> None:
        write(org_tree / "vexar-mcp.toml", 'extends = "teams/payments.toml"\n')
        cfg = load_config(
            start_dir=org_tree,
            min_score_override=60,
            max_tokens_override=400,
        )
        assert cfg.min_score == 60
        assert cfg.max_tool_tokens == 400
        assert dict(cfg.policy_sources)["max-tool-tokens"] == "cli"

    def test_extends_path_is_relative_to_the_declaring_file(self, org_tree: Path) -> None:
        """A resolving-relative-to-cwd implementation would fail when run from elsewhere."""
        write(org_tree / "vexar-mcp.toml", 'extends = "teams/payments.toml"\n')
        cfg = load_config(config_path=org_tree / "vexar-mcp.toml", start_dir=Path("/"))
        assert cfg.min_score == 95
        assert sorted(cfg.ignored_rules) == ["MCP004", "MCP008"]

    def test_extends_a_pyproject_table(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(
            tmp_path / "org" / "pyproject.toml",
            '[project]\nname = "org"\n\n[tool.vexar-mcp]\npolicy = "ci"\nmin-score = 88\n',
        )
        write(tmp_path / "vexar-mcp.toml", 'extends = "org/pyproject.toml"\n')
        cfg = load_config(start_dir=tmp_path)
        assert cfg.policy_name == "ci"
        assert cfg.min_score == 88
        assert any("[tool.vexar-mcp]" in origin for origin, _ in cfg.policy_chain)


class TestProtectedCategoriesAreMonotonic:
    def test_child_cannot_ignore_a_rule_an_ancestor_protected(self, tmp_path: Path) -> None:
        """The acceptance criterion, stated as the attack it prevents."""
        (tmp_path / ".git").mkdir()
        write(tmp_path / "org.toml", 'policy = "strict-prod"\n')
        write(tmp_path / "vexar-mcp.toml", 'extends = "org.toml"\nignore = ["MCP005"]\n')

        with pytest.raises(PolicyViolation, match="cannot be ignored") as excinfo:
            load_config(start_dir=tmp_path)
        # The error must name the file that tried, not the ancestor that protected it.
        assert "vexar-mcp.toml" in str(excinfo.value)

    def test_child_switching_to_a_weaker_preset_still_keeps_protection(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "org.toml", 'policy = "enterprise-governance"\n')
        write(
            tmp_path / "vexar-mcp.toml",
            'extends = "org.toml"\npolicy = "default"\nignore = ["MCP001"]\n',
        )
        with pytest.raises(PolicyViolation, match="MCP001") as excinfo:
            load_config(start_dir=tmp_path)
        # The message must not claim the *effective* preset protects these rules: `default`
        # protects nothing, and saying otherwise sends the reader to the wrong file.
        message = str(excinfo.value)
        assert "inherits protection" in message
        assert "does not protect them itself" in message

    def test_protection_accumulates_across_the_chain(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "org.toml", 'policy = "ci"\n')  # protects security
        write(tmp_path / "team.toml", 'extends = "org.toml"\npolicy = "enterprise-governance"\n')
        write(tmp_path / "vexar-mcp.toml", 'extends = "team.toml"\npolicy = "default"\n')
        cfg = load_config(start_dir=tmp_path)
        assert cfg.protected_categories == ("contract", "security")
        assert cfg.policy_name == "default"  # the preset the file actually asked for

    def test_cli_cannot_lift_protection_a_committed_file_declared(self, tmp_path: Path) -> None:
        """A flag is ephemeral; the reviewed configuration is not. Editing the file is the
        only way to narrow protection, and that edit is visible in review."""
        (tmp_path / ".git").mkdir()
        write(tmp_path / "vexar-mcp.toml", 'policy = "strict-prod"\n')
        with pytest.raises(PolicyViolation, match="MCP005"):
            load_config(
                start_dir=tmp_path, policy_override="default", extra_ignored_rules={"MCP005"}
            )

    def test_without_a_config_file_the_cli_can_still_run_leniently(self, tmp_path: Path) -> None:
        """Protection is inherited from configuration, not imposed globally."""
        (tmp_path / ".git").mkdir()
        cfg = load_config(
            start_dir=tmp_path, policy_override="default", extra_ignored_rules={"MCP005"}
        )
        assert cfg.policy_name == "default"
        assert cfg.protected_categories == ()
        assert "MCP005" in cfg.ignored_rules

    def test_protected_rule_cannot_be_ignored_from_a_preset_layer_alone(self) -> None:
        layers = [PolicyLayer(origin="org.toml", preset_name="strict-prod", ignore=("MCP005",))]
        with pytest.raises(PolicyViolation, match="org.toml"):
            resolve_policy_layers(layers, catalog=CATALOG)


class TestErrorHandling:
    def test_extends_cycle_is_reported_with_the_cycle(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "a.toml", 'extends = "b.toml"\n')
        write(tmp_path / "b.toml", 'extends = "a.toml"\n')
        with pytest.raises(PolicyViolation, match="cycle") as excinfo:
            load_config(start_dir=tmp_path, config_path=tmp_path / "a.toml")
        assert "a.toml" in str(excinfo.value) and "b.toml" in str(excinfo.value)

    def test_self_extends_is_a_cycle(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "vexar-mcp.toml", 'extends = "vexar-mcp.toml"\n')
        with pytest.raises(PolicyViolation, match="cycle"):
            load_config(start_dir=tmp_path)

    def test_unknown_extends_target_names_both_interpretations(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "vexar-mcp.toml", 'extends = "lennient"\n')
        with pytest.raises(PolicyViolation, match="neither a built-in preset") as excinfo:
            load_config(start_dir=tmp_path)
        message = str(excinfo.value)
        assert "strict-prod" in message  # lists the valid presets
        assert "lennient" in message

    def test_non_string_extends_is_rejected(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "vexar-mcp.toml", "extends = 3\n")
        with pytest.raises(ValueError, match="'extends' must be"):
            load_config(start_dir=tmp_path)

    def test_unknown_key_in_an_ancestor_names_that_file(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "org.toml", "max_tokens = 10\n")
        write(tmp_path / "vexar-mcp.toml", 'extends = "org.toml"\n')
        with pytest.raises(ValueError, match="unknown configuration key") as excinfo:
            load_config(start_dir=tmp_path)
        assert "org.toml" in str(excinfo.value)

    def test_invalid_fail_on_in_an_ancestor_names_that_file(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "org.toml", 'fail-on = "sometimes"\n')
        write(tmp_path / "vexar-mcp.toml", 'extends = "org.toml"\n')
        with pytest.raises(ValueError, match="invalid 'fail-on'") as excinfo:
            load_config(start_dir=tmp_path)
        assert "org.toml" in str(excinfo.value)

    def test_pyproject_without_the_project_table_cannot_be_extended(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "other" / "pyproject.toml", '[project]\nname = "x"\n')
        write(tmp_path / "vexar-mcp.toml", 'extends = "other/pyproject.toml"\n')
        with pytest.raises(ValueError, match="no .* table to inherit from"):
            load_config(start_dir=tmp_path)


class TestResolutionChainReporting:
    def test_chain_lists_every_origin_root_first(self, org_tree: Path) -> None:
        write(org_tree / "vexar-mcp.toml", 'extends = "teams/payments.toml"\n')
        cfg = load_config(start_dir=org_tree)
        origins = [origin for origin, _ in cfg.policy_chain]
        assert len(origins) == 3
        assert origins[0].endswith("org.toml")
        assert origins[1].endswith("payments.toml")
        assert origins[2].endswith("vexar-mcp.toml")

    def test_chain_reports_what_each_layer_contributed(self, org_tree: Path) -> None:
        write(org_tree / "vexar-mcp.toml", 'extends = "teams/payments.toml"\n')
        contributed = dict(load_config(start_dir=org_tree).policy_chain)
        assert contributed[next(o for o in contributed if o.endswith("org.toml"))] == (
            "policy",
            "max-tool-tokens",
            "max-server-tokens",
            "ignore",
            "exclude",
        )
        assert contributed[next(o for o in contributed if o.endswith("payments.toml"))] == (
            "min-score",
            "ignore",
            "exclude",
        )

    def test_sources_name_the_winning_layer_for_every_setting(self, org_tree: Path) -> None:
        write(org_tree / "vexar-mcp.toml", 'extends = "teams/payments.toml"\nmin-score = 93\n')
        sources = dict(load_config(start_dir=org_tree).policy_sources)
        assert sources["min-score"].endswith("vexar-mcp.toml")
        assert sources["policy"].endswith("org.toml")
        assert sources["max-tool-tokens"].endswith("org.toml")

    def test_extends_preset_appears_as_its_own_root_origin(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "vexar-mcp.toml", 'extends = "strict-prod"\nmax-tool-tokens = 175\n')
        cfg = load_config(start_dir=tmp_path)
        assert "extends 'strict-prod'" in cfg.policy_chain[0][0]
        assert cfg.policy_chain[1][0].endswith("vexar-mcp.toml")

    def test_describe_config_exposes_the_chain_and_sources(self, org_tree: Path) -> None:
        write(org_tree / "vexar-mcp.toml", 'extends = "teams/payments.toml"\n')
        payload = describe_config(load_config(start_dir=org_tree))
        assert [step["contributed"] for step in payload["resolution"]["chain"]][1] == [
            "min-score",
            "ignore",
            "exclude",
        ]
        assert payload["resolution"]["sources"]["policy"].endswith("org.toml")
        # The flat keys stay for backward compatibility with existing consumers.
        assert payload["policy"] == "strict-prod"
        assert payload["protected_categories"] == ["security"]


class TestPrintConfigCommand:
    def _scan_target(self, root: Path) -> str:
        target = root / "server.py"
        target.write_text("x = 1\n", encoding="utf-8")
        return str(target)

    def test_print_config_shows_the_inheritance_chain(
        self, org_tree: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write(org_tree / "vexar-mcp.toml", 'extends = "teams/payments.toml"\nmin-score = 93\n')
        assert main(["scan", self._scan_target(org_tree), "--print-config"]) == 0
        payload = json.loads(capsys.readouterr().out)

        chain = payload["resolution"]["chain"]
        assert [step["origin"].rsplit("/", 1)[-1] for step in chain] == [
            "org.toml",
            "payments.toml",
            "vexar-mcp.toml",
        ]
        assert payload["resolution"]["sources"]["min-score"].endswith("vexar-mcp.toml")
        assert payload["min_score"] == 93
        assert payload["config_file"].endswith("vexar-mcp.toml")

    def test_print_config_records_cli_as_a_layer(
        self, org_tree: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write(org_tree / "vexar-mcp.toml", 'extends = "teams/payments.toml"\n')
        assert (
            main(["scan", self._scan_target(org_tree), "--print-config", "--min-score", "70"]) == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["resolution"]["chain"][-1]["origin"] == "cli"
        assert payload["resolution"]["sources"]["min-score"] == "cli"
        assert payload["min_score"] == 70

    def test_unreadable_extends_is_a_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A broken chain must exit 2 and say which target could not be resolved."""
        (tmp_path / ".git").mkdir()
        write(tmp_path / "vexar-mcp.toml", 'extends = "missing.toml"\n')
        target = tmp_path / "server.py"
        target.write_text("x = 1\n", encoding="utf-8")
        assert main(["scan", str(target), "--print-config"]) == 2
        assert "neither a built-in preset" in capsys.readouterr().err


class TestBackwardCompatibility:
    def test_a_config_without_extends_resolves_as_a_single_layer(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "vexar-mcp.toml", 'policy = "ci"\nmin-score = 85\n')
        cfg = load_config(start_dir=tmp_path)
        assert len(cfg.policy_chain) == 1
        assert cfg.policy_name == "ci"
        assert cfg.min_score == 85
        assert cfg.protected_categories == (Category.SECURITY.value,)

    def test_no_config_file_leaves_the_chain_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        cfg = load_config(start_dir=tmp_path)
        assert cfg.policy_chain == ()
        assert cfg.policy_name == "default"
        assert cfg.protected_categories == ()

    def test_legacy_config_filename_inherits_the_same_way(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "org.toml", 'policy = "ci"\n')
        write(tmp_path / ".mcp-doctor.toml", 'extends = "org.toml"\nmin-score = 91\n')
        cfg = load_config(start_dir=tmp_path)
        assert cfg.policy_name == "ci"
        assert cfg.min_score == 91
