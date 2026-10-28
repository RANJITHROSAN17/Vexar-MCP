"""Tests for `vexar-mcp init`: the scaffold, and the refusals around it.

`init` exists to make adoption one command, so the failure modes matter more than the
happy path. Three of them are load-bearing and each has a dedicated test:

* a generated config must load through the production loader -- a scaffold the tool
  rejects is worse than no scaffold;
* a refusal must write *nothing*, so a half-scaffolded project is not a reachable state;
* a config file must never silently shadow an existing project configuration, because
  the loader prefers `vexar-mcp.toml` and would change how existing projects are scanned.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import tomllib
import yaml

from mcp_doctor import __version__
from mcp_doctor.cli import main
from mcp_doctor.config import discover_config, load_config
from mcp_doctor.models import Severity
from mcp_doctor.scaffold import (
    ACTION_REPOSITORY,
    CONFIG_FILENAME,
    WORKFLOW_PATH,
    plan_scaffold,
    render_config_text,
    render_workflow_text,
)

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "validate_workflows", ROOT / ".github" / "scripts" / "validate_workflows.py"
)
assert _spec is not None and _spec.loader is not None
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)


def _write_workflow(project: Path, text: str) -> Path:
    path = project / WORKFLOW_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestGeneratedConfig:
    def test_it_loads_through_the_production_loader(self, tmp_path: Path) -> None:
        path = tmp_path / CONFIG_FILENAME
        path.write_text(render_config_text(), encoding="utf-8")
        config = load_config(config_path=path, start_dir=tmp_path)
        assert config.policy_name == "default"

    def test_the_documented_defaults_match_the_tool(self) -> None:
        """The comments claim these are the effective defaults, so they must be."""
        data = tomllib.loads(render_config_text())
        effective = load_config(start_dir=ROOT)
        assert data["max-tool-tokens"] == effective.max_tool_tokens
        assert data["max-server-tokens"] == effective.max_server_tokens
        assert data["min-score"] == effective.min_score
        assert Severity(data["fail-on"]) is effective.fail_on

    def test_exclude_and_ignore_are_empty_so_nothing_is_silenced(self) -> None:
        data = tomllib.loads(render_config_text())
        assert data["ignore"] == [], "a scaffold must not pre-silence a rule"
        # An empty list keeps the built-in skip list; a non-empty one replaces it.
        assert data["exclude"] == []

    def test_it_names_the_built_in_skip_list_it_would_replace(self) -> None:
        """`exclude` replaces rather than merges, so the trap has to be visible."""
        text = render_config_text()
        for name in (".git", "node_modules", "__pycache__"):
            assert name in text
        assert "REPLACES" in text

    def test_every_known_config_key_is_documented(self) -> None:
        from mcp_doctor.config import _KNOWN_KEYS

        text = render_config_text()
        for key in sorted(_KNOWN_KEYS):
            assert key in text, f"config key '{key}' is not mentioned in the scaffold"

    @pytest.mark.parametrize("preset", ["ci", "strict-prod", "enterprise-governance"])
    def test_it_writes_the_chosen_presets_gate_values(self, preset: str, tmp_path: Path) -> None:
        """Switching presets must change the written values, not just the `policy` line."""
        from mcp_doctor.policies import resolve_policy

        policy = resolve_policy(preset)
        data = tomllib.loads(render_config_text(preset))
        assert data["policy"] == preset
        assert data["max-tool-tokens"] == policy.max_tool_tokens
        assert data["max-server-tokens"] == policy.max_server_tokens
        assert data["min-score"] == policy.min_score
        assert data["fail-on"] == policy.fail_on.value

        path = tmp_path / CONFIG_FILENAME
        path.write_text(render_config_text(preset), encoding="utf-8")
        config = load_config(config_path=path, start_dir=tmp_path)
        assert config.policy_name == preset
        assert config.max_tool_tokens == policy.max_tool_tokens

    def test_an_unknown_preset_is_rejected(self) -> None:
        from mcp_doctor.policies import PolicyViolation

        with pytest.raises(PolicyViolation):
            render_config_text("no-such-preset")


class TestGeneratedWorkflow:
    def test_it_passes_the_repositorys_own_workflow_validator(self, tmp_path: Path) -> None:
        """A generated workflow should satisfy the rules this project enforces on itself."""
        path = _write_workflow(tmp_path, render_workflow_text("v0.2.3"))
        assert validator.validate_workflow(path) == []

    def test_the_default_ref_is_main_as_the_issue_specifies(self) -> None:
        doc = yaml.safe_load(render_workflow_text())
        uses = [step["uses"] for step in doc["jobs"]["vexar-mcp"]["steps"] if "uses" in step]
        assert f"{ACTION_REPOSITORY}@main" in uses

    def test_a_moving_branch_ref_is_the_only_thing_the_validator_objects_to(
        self, tmp_path: Path
    ) -> None:
        """`@main` is the documented default, so the objection must be scoped and known."""
        path = _write_workflow(tmp_path, render_workflow_text())
        errors = validator.validate_workflow(path)
        assert len(errors) == 1
        assert "moving branch" in errors[0]

    def test_it_warns_in_the_file_when_the_ref_is_unpinned(self) -> None:
        assert "Pin a released tag" in render_workflow_text()
        assert "Pin a released tag" not in render_workflow_text("v0.2.3")

    def test_it_gates_with_a_real_policy_and_uploads_sarif(self) -> None:
        doc = yaml.safe_load(render_workflow_text())
        steps = doc["jobs"]["vexar-mcp"]["steps"]
        scan = next(step for step in steps if step.get("id") == "vexar")
        assert scan["with"]["policy"] == "ci"
        assert scan["with"]["sarif-output"] == "vexar-mcp.sarif"
        assert any("upload-sarif@v4" in step.get("uses", "") for step in steps)

    def test_the_upload_step_runs_even_when_the_gate_fails(self) -> None:
        doc = yaml.safe_load(render_workflow_text())
        upload = next(
            step
            for step in doc["jobs"]["vexar-mcp"]["steps"]
            if "uses" in step and "upload-sarif" in step["uses"]
        )
        assert upload["if"] == "always()", "findings are exactly when the upload matters"


class TestPlanAndRefusal:
    def test_a_fresh_directory_is_writable(self, tmp_path: Path) -> None:
        plan = plan_scaffold(tmp_path)
        assert not plan.is_blocked
        assert [file.path.name for file in plan.files] == [CONFIG_FILENAME]

    def test_a_missing_directory_is_refused_not_created(self, tmp_path: Path) -> None:
        plan = plan_scaffold(tmp_path / "absent")
        assert plan.is_blocked
        assert "does not exist" in plan.problems[0]
        assert not (tmp_path / "absent").exists()

    def test_a_file_target_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "server.py"
        target.write_text("", encoding="utf-8")
        assert plan_scaffold(target).is_blocked

    def test_an_existing_config_is_refused_without_force(self, tmp_path: Path) -> None:
        (tmp_path / CONFIG_FILENAME).write_text("# mine\n", encoding="utf-8")
        plan = plan_scaffold(tmp_path)
        assert plan.is_blocked
        assert "--force" in plan.problems[0]

    def test_an_existing_legacy_config_is_not_silently_shadowed(self, tmp_path: Path) -> None:
        """`vexar-mcp.toml` wins over `.mcp-doctor.toml`, so writing one changes behaviour."""
        legacy = tmp_path / ".mcp-doctor.toml"
        legacy.write_text('policy = "strict-prod"\n', encoding="utf-8")
        plan = plan_scaffold(tmp_path, github_action=True)
        assert plan.is_blocked
        assert any(".mcp-doctor.toml" in problem for problem in plan.problems)
        assert not (tmp_path / CONFIG_FILENAME).exists()

    def test_a_pyproject_table_also_blocks(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.vexar-mcp]\npolicy = "ci"\n', encoding="utf-8"
        )
        plan = plan_scaffold(tmp_path)
        assert plan.is_blocked
        assert any("pyproject.toml" in problem for problem in plan.problems)

    def test_force_lifts_both_the_overwrite_and_the_shadow_refusal(self, tmp_path: Path) -> None:
        (tmp_path / CONFIG_FILENAME).write_text("# mine\n", encoding="utf-8")
        (tmp_path / ".mcp-doctor.toml").write_text('policy = "ci"\n', encoding="utf-8")
        assert not plan_scaffold(tmp_path, force=True).is_blocked


class TestInitCommand:
    def test_fresh_creation(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["init", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "wrote" in out
        config = tmp_path / CONFIG_FILENAME
        assert config.is_file()
        assert tomllib.loads(config.read_text(encoding="utf-8"))["policy"] == "default"
        assert not (tmp_path / WORKFLOW_PATH).exists(), "--github-action was not passed"

    def test_github_action_writes_the_workflow(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["init", str(tmp_path), "--github-action"]) == 0
        capsys.readouterr()
        workflow = tmp_path / WORKFLOW_PATH
        assert workflow.is_file()
        assert ACTION_REPOSITORY in workflow.read_text(encoding="utf-8")

    def test_existing_files_are_protected_and_nothing_is_written(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = tmp_path / CONFIG_FILENAME
        config.write_text("# hand-written, must survive\n", encoding="utf-8")
        assert main(["init", str(tmp_path), "--github-action"]) == 2
        err = capsys.readouterr().err
        assert "--force" in err
        assert config.read_text(encoding="utf-8") == "# hand-written, must survive\n"
        # The workflow did not exist and must not be created by a refused run.
        assert not (tmp_path / WORKFLOW_PATH).exists()

    def test_force_overwrites(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = tmp_path / CONFIG_FILENAME
        config.write_text("# stale\n", encoding="utf-8")
        assert main(["init", str(tmp_path), "--force"]) == 0
        capsys.readouterr()
        assert "stale" not in config.read_text(encoding="utf-8")

    def test_dry_run_writes_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["init", str(tmp_path), "--dry-run", "--github-action"]) == 0
        out = capsys.readouterr().out
        assert "policy = " in out and "name: Vexar-MCP" in out
        assert list(tmp_path.iterdir()) == []

    def test_the_generated_config_is_what_a_later_scan_uses(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """End to end: scaffold, then scan, and confirm the file was picked up."""
        (tmp_path / "server.py").write_text(
            "from mcp.server.fastmcp import FastMCP\n"
            'mcp = FastMCP("s")\n\n'
            "@mcp.tool()\n"
            "def read_note(path: str) -> str:\n"
            '    """Read a note from disk and return its text."""\n'
            "    return open(path).read()\n",
            encoding="utf-8",
        )
        assert main(["init", str(tmp_path), "--policy", "strict-prod"]) == 0
        capsys.readouterr()
        assert discover_config(tmp_path) == tmp_path / CONFIG_FILENAME
        effective = load_config(start_dir=tmp_path)
        assert effective.policy_name == "strict-prod"
        assert main(["scan", str(tmp_path)]) in {0, 1, 3}
        capsys.readouterr()

    def test_an_unknown_policy_is_a_configuration_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["init", str(tmp_path), "--policy", "nope"]) == 2
        assert "unknown policy preset" in capsys.readouterr().err
        assert list(tmp_path.iterdir()) == []

    def test_pinning_the_ref_is_reported_and_recorded(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["init", str(tmp_path), "--github-action", "--action-ref", "v0.2.3"]) == 0
        err = capsys.readouterr().err
        assert "moving branch" not in err
        text = (tmp_path / WORKFLOW_PATH).read_text(encoding="utf-8")
        assert f"{ACTION_REPOSITORY}@v0.2.3" in text

    def test_the_moving_ref_note_goes_to_stderr_and_is_reproducible(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["init", str(tmp_path), "--github-action"]) == 0
        err = capsys.readouterr().err
        assert "moving branch" in err, "an unpinned workflow should be called out"

    def test_the_scaffold_is_stable_across_runs(self, tmp_path: Path) -> None:
        """Regenerating must not churn the diff; otherwise it is regenerated by nobody."""
        first = render_config_text()
        assert first == render_config_text()
        assert __version__ in first, "provenance should name the generating version"
