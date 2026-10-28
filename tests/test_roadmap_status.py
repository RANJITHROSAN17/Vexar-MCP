"""Tests for the status bot, the PR guide, and the rule that bots must actually do something."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".github" / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def manifest() -> dict:
    return json.loads((ROOT / ".github" / "roadmap.json").read_text(encoding="utf-8"))


# ── the plan document ───────────────────────────────────────────────────────────────────────


def test_the_status_issue_links_a_document_that_exists() -> None:
    plan = manifest().get("plan_doc")
    assert plan, "the manifest must name the plan document the status issue links to"
    assert (ROOT / plan).is_file(), f"{plan} is linked from a bot comment but is not in the tree"


def test_every_queued_item_can_be_agreed_to_be_done() -> None:
    for item in manifest()["items"]:
        if item.get("status") == "shipped":
            continue
        assert item.get("acceptance_criteria"), f"{item['id']} has no acceptance criteria"


# ── status ──────────────────────────────────────────────────────────────────────────────────


def test_next_up_is_in_manifest_order_when_no_priority_is_declared() -> None:
    mod = load("roadmap_status")
    data = {"items": [{"id": "A", "title": "a"}, {"id": "B", "title": "b"}]}
    assert [item["id"] for item in mod.pick_next(data, [])] == ["A", "B"]


def test_next_up_skips_an_item_that_already_has_an_open_issue() -> None:
    mod = load("roadmap_status")
    data = {"items": [{"id": "A", "title": "a"}, {"id": "B", "title": "b"}]}
    issues = [{"state": "open", "body": "<!-- roadmap-id: A -->"}]
    assert [item["id"] for item in mod.pick_next(data, issues)] == ["B"]


def test_status_reports_a_version_that_was_never_released() -> None:
    """The real drift in this repository: pyproject is ahead of the newest tag."""
    mod = load("roadmap_status")
    assert mod.version_drift("0.2.6", "v0.2.1") is not None
    assert mod.version_drift("0.2.6", "v0.2.6") is None


def test_status_flags_a_plan_naming_a_deleted_file(tmp_path: Path) -> None:
    mod = load("roadmap_status")
    data = {"items": [{"id": "A", "title": "a", "files": ["src/mcp_doctor/gone.py"]}]}
    assert mod.file_drift(data, tmp_path)
    (tmp_path / "src" / "mcp_doctor").mkdir(parents=True)
    (tmp_path / "src" / "mcp_doctor" / "gone.py").write_text("")
    assert not mod.file_drift(data, tmp_path)


def test_status_is_marked_so_it_is_edited_and_not_duplicated() -> None:
    mod = load("roadmap_status")
    body = mod.render_status(manifest(), [], root=ROOT, version=None, tag=None)
    assert body.startswith(mod.STATUS_MARKER)


# ── PR guide ───────────────────────────────────────────────────────────────────────────────


def test_pr_guide_matches_a_named_file() -> None:
    mod = load("pr_context")
    data = {"items": [{"id": "RM-023", "title": "t", "files": ["pyproject.toml"]}]}
    assert [item["id"] for item, _, _ in mod.match_items(data, ["pyproject.toml"])] == ["RM-023"]


def test_pr_guide_matches_a_test_written_for_a_named_module() -> None:
    mod = load("pr_context")
    data = {"items": [{"id": "X", "title": "t", "files": ["src/mcp_doctor/report.py"]}]}
    assert mod.match_items(data, ["tests/test_report.py"])[0][0]["id"] == "X"


def test_pr_guide_says_nothing_matched_rather_than_picking_the_closest() -> None:
    mod = load("pr_context")
    text = mod.render_context(
        {"items": [{"id": "X", "title": "t", "files": ["a.py"]}]}, ["README.md"], ""
    )
    assert "No roadmap item matches" in text


# ── bot hygiene ────────────────────────────────────────────────────────────────────────────


def test_no_workflow_is_a_placeholder() -> None:
    """A workflow whose only action is to echo a description of itself is not a bot.

    Two of these shipped here (issue-raise-bot, issue-resolve-bot) and read as coverage for
    months. If a workflow cannot do its job, it should be deleted rather than narrated.
    """
    import yaml

    offenders: list[str] = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for name, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            echo_only = 0
            for step in steps:
                run = step.get("run")
                if not run:
                    continue
                body = [
                    line.strip()
                    for line in str(run).splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
                if body and all(line.startswith("echo ") for line in body):
                    echo_only += 1
            # A conditional "nothing to do" note is fine; a job that only ever narrates is not.
            if steps and echo_only == len(steps):
                offenders.append(f"{path.name}: job '{name}' only echoes")
    assert not offenders, "\n".join(offenders)
