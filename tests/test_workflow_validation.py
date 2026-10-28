"""Tests for the repository's own workflow linter.

GitHub surfaces an invalid workflow as a failed run with no jobs, which reads as a bot
malfunction instead of a syntax error. These tests keep the linter honest so CI can fail
first, with a message that names the file and the field.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "validate_workflows.py"

spec = importlib.util.spec_from_file_location("validate_workflows", SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules["validate_workflows"] = mod
spec.loader.exec_module(mod)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_every_repository_workflow_is_valid() -> None:
    """The live repository must pass its own linter."""
    assert mod.main(["--quiet"]) == 0


def test_action_manifest_is_valid() -> None:
    assert mod.validate_action(ROOT / "action.yml") == []


def test_unquoted_colon_in_step_name_is_reported(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "broken.yml",
        """
name: Broken
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Install tools (fix: venv gone)
        run: echo hi
""",
    )
    errors = mod.validate_workflow(path)
    assert errors and "YAML syntax error" in errors[0]
    assert "line" in errors[0]


def test_invalid_permission_scope_is_reported_with_a_hint(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "perms.yml",
        """
name: Perms
on: push
permissions:
  pull_requests: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    errors = mod.validate_workflow(path)
    assert any("unknown permission scope 'pull_requests'" in e for e in errors)
    assert any("pull-requests" in e for e in errors)


def test_job_permission_scopes_are_checked(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "jobperms.yml",
        """
name: JobPerms
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull_requests: read
    steps:
      - run: echo hi
""",
    )
    errors = mod.validate_workflow(path)
    assert any("job 'build' permissions" in e for e in errors)


def test_missing_runs_on_and_steps_are_reported(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "empty.yml",
        """
name: Empty
on: push
jobs:
  build:
    steps: []
""",
    )
    errors = mod.validate_workflow(path)
    assert any("missing 'runs-on'" in e for e in errors)
    assert any("has no steps" in e for e in errors)


def test_unpinned_action_reference_is_reported(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "unpinned.yml",
        """
name: Unpinned
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout
      - uses: some/action@main
""",
    )
    errors = mod.validate_workflow(path)
    assert any("not pinned to a ref" in e for e in errors)
    assert any("moving branch" in e for e in errors)


def test_local_action_and_pinned_refs_are_allowed(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "good.yml",
        """
name: Good
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: ./
      - run: echo hi
""",
    )
    assert mod.validate_workflow(path) == []


def test_workflow_without_jobs_is_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, "nojobs.yml", "name: NoJobs\non: push\n")
    assert any("no 'jobs' mapping" in e for e in mod.validate_workflow(path))


def test_non_mapping_workflow_is_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, "list.yml", "- just\n- a list\n")
    assert any("must be a mapping" in e for e in mod.validate_workflow(path))


def test_reusable_workflow_job_is_accepted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "reusable.yml",
        """
name: Reusable
on: push
jobs:
  call:
    uses: org/repo/.github/workflows/ci.yml@v1
""",
    )
    assert mod.validate_workflow(path) == []


@pytest.mark.parametrize("preset", ["read-all", "write-all"])
def test_permission_presets_are_accepted(tmp_path: Path, preset: str) -> None:
    path = _write(
        tmp_path,
        f"preset-{preset}.yml",
        f"""
name: Preset
on: push
permissions: {preset}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    assert mod.validate_workflow(path) == []


def test_bogus_permission_preset_is_reported(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "badpreset.yml",
        """
name: BadPreset
on: push
permissions: write-some
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    assert any("not a valid permission preset" in e for e in mod.validate_workflow(path))


def test_main_fails_when_a_workflow_is_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "broken.yml", "name: x\non: push\njobs: {}\n")
    monkeypatch.setattr(mod, "ACTIONS_DIR", tmp_path)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    assert mod.main([]) == 1


def test_action_manifest_requires_composite_runs(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "action.yml",
        """
name: Thing
description: Thing
runs:
  using: composite
  steps:
    - run: echo hi
""",
    )
    assert mod.validate_action(path) == []

    bad = _write(
        tmp_path,
        "bad-action.yml",
        """
name: Thing
description: Thing
runs:
  using: node20
  steps: []
""",
    )
    errors = mod.validate_action(bad)
    assert any("runs.using: composite" in e for e in errors)
    assert any("'runs.steps' is empty" in e for e in errors)


def test_action_manifest_missing_fields(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty-action.yml", "name: OnlyName\n")
    errors = mod.validate_action(path)
    assert any("missing required field 'description'" in e for e in errors)
    assert any("missing required field 'runs'" in e for e in errors)


# --------------------------------------------------------------------------------------
# AI review report script
# --------------------------------------------------------------------------------------

AI_SCRIPT = ROOT / ".github" / "scripts" / "ai_review_report.py"


def _load_ai_report():
    spec = importlib.util.spec_from_file_location("ai_review_report", AI_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ai_review_report"] = module
    spec.loader.exec_module(module)
    return module


def test_ai_review_report_reports_findings() -> None:
    module = _load_ai_report()
    report = module.build_report(ROOT / "examples")
    assert "AI Code Reviewer" in report
    assert "| MCP001 |" in report
    assert "Risk index" in report
    assert "### Enhancements" in report


def test_ai_review_report_handles_clean_input(tmp_path: Path) -> None:
    module = _load_ai_report()
    (tmp_path / "empty.py").write_text("x = 1\n", encoding="utf-8")
    report = module.build_report(tmp_path)
    assert "No security, schema, contract, or token-budget findings" in report
    assert "Nothing outstanding" in report


def test_ai_review_report_main_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_ai_report()
    monkeypatch.chdir(tmp_path)
    assert module.main([str(ROOT / "examples")]) == 0
    written = (tmp_path / "ai_review_report.md").read_text(encoding="utf-8")
    assert "Vexar-MCP" in written


def test_ai_review_report_truncates_large_results(tmp_path: Path) -> None:
    module = _load_ai_report()
    monkeypatch_limit = 1
    original = module.MAX_DIAGNOSTICS
    module.MAX_DIAGNOSTICS = monkeypatch_limit
    try:
        report = module.build_report(ROOT / "examples")
    finally:
        module.MAX_DIAGNOSTICS = original
    assert "further finding(s) omitted" in report
