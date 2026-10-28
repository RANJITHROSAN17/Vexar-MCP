"""Unit tests for the autonomous GitHub Roadmap & Issue Curator Bot."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / ".github" / "scripts" / "sync_roadmap_issues.py"

spec = importlib.util.spec_from_file_location("sync_roadmap_issues", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
bot_mod = importlib.util.module_from_spec(spec)
sys.modules["sync_roadmap_issues"] = bot_mod
spec.loader.exec_module(bot_mod)


def test_roadmap_json_is_valid_and_unique() -> None:
    roadmap = json.loads((ROOT / ".github" / "roadmap.json").read_text(encoding="utf-8"))
    ids = [item["id"] for item in roadmap["items"]]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 12
    label_names = {lbl["name"] for lbl in roadmap["labels"]}
    for item in roadmap["items"]:
        assert set(item["labels"]).issubset(label_names)
        rendered = bot_mod.render_issue_body(item)
        assert f"<!-- roadmap-id: {item['id']} -->" in rendered


def test_queue_replenishment_deduplicates_open_and_closed_issues() -> None:
    roadmap = {
        "max_open_roadmap_issues": 2,
        "items": [
            {"id": "RM-001", "title": "first", "summary": "s", "motivation": "m"},
            {"id": "RM-002", "title": "second", "summary": "s", "motivation": "m"},
            {"id": "RM-003", "title": "third", "summary": "s", "motivation": "m"},
        ],
    }
    existing = [
        {"state": "closed", "body": "<!-- roadmap-id: RM-001 -->\ndone"},
        {"state": "open", "body": "<!-- roadmap-id: RM-002 -->\nin progress"},
    ]
    queued = bot_mod.select_items_to_open(roadmap, existing)
    assert [i["id"] for i in queued] == ["RM-003"]


def test_shipped_items_are_never_opened() -> None:
    roadmap = {
        "max_open_roadmap_issues": 5,
        "items": [
            {
                "id": "RM-001",
                "title": "done",
                "summary": "s",
                "motivation": "m",
                "status": "shipped",
            },
            {
                "id": "RM-002",
                "title": "todo",
                "summary": "s",
                "motivation": "m",
                "status": "queued",
            },
        ],
    }
    to_open = bot_mod.select_items_to_open(roadmap, [])
    assert [item["id"] for item in to_open] == ["RM-002"]


def test_shipped_issues_are_closed_and_others_left_alone() -> None:
    roadmap = {
        "items": [
            {"id": "RM-001", "status": "shipped", "shipped_in": "0.2.1"},
            {"id": "RM-002", "status": "queued"},
        ]
    }
    issues = [
        {"number": 10, "state": "open", "body": "<!-- roadmap-id: RM-001 -->\nbody"},
        {"number": 11, "state": "open", "body": "<!-- roadmap-id: RM-002 -->\nbody"},
        {"number": 12, "state": "closed", "body": "<!-- roadmap-id: RM-001 -->\nbody"},
        {"number": 13, "state": "open", "body": "no marker"},
        {
            "number": 14,
            "state": "open",
            "body": "<!-- roadmap-id: RM-001 -->",
            "pull_request": {"url": "x"},
        },
    ]
    to_close = bot_mod.select_shipped_issues_to_close(roadmap, issues)
    assert [number for number, _ in to_close] == [10]
    comment = bot_mod.render_shipped_comment(to_close[0][1])
    assert "0.2.1" in comment


def test_drifted_open_issues_are_refreshed_and_shipped_ones_are_not() -> None:
    roadmap = {
        "items": [
            {"id": "RM-001", "title": "new title", "summary": "s", "motivation": "m"},
            {
                "id": "RM-002",
                "title": "shipped one",
                "summary": "s",
                "motivation": "m",
                "status": "shipped",
            },
        ]
    }
    issues = [
        {
            "number": 1,
            "state": "open",
            "title": "old title",
            "body": "<!-- roadmap-id: RM-001 -->\nold",
        },
        {"number": 2, "state": "open", "title": "ship", "body": "<!-- roadmap-id: RM-002 -->"},
        {
            "number": 3,
            "state": "open",
            "title": "new title",
            "body": bot_mod.render_issue_body(roadmap["items"][0]),
        },
    ]
    stale = bot_mod.select_issues_to_refresh(roadmap, issues)
    assert [number for number, _ in stale] == [1]


def test_roadmap_never_proposes_a_rule_id_that_already_exists() -> None:
    """A queued item proposing a live rule ID asks a contributor to rebuild shipped work.

    Mentions of existing rules in passing ("taint propagation to strengthen MCP005") are fine;
    what must not drift is an item that *proposes to add* an ID that is already taken.
    """
    from mcp_doctor.rules import get_rule_catalog

    roadmap = json.loads((ROOT / ".github" / "roadmap.json").read_text(encoding="utf-8"))
    live_ids = set(get_rule_catalog())
    offenders = []
    for item in roadmap["items"]:
        if item.get("status") == "shipped":
            continue
        for rule_id in set(re.findall(r"MCP\d{3}", item["title"])):
            proposes = re.search(rf"add\s+{rule_id}\b", item["title"]) or re.search(
                rf"{rule_id}\s+rule", item["title"]
            )
            if proposes and rule_id in live_ids:
                offenders.append((item["id"], rule_id))
    assert not offenders, f"queued roadmap items propose already-registered rules: {offenders}"


def test_shipped_items_name_a_version_that_exists_in_the_changelog() -> None:
    """`shipped_in` is a claim about the changelog, so the changelog must agree."""
    roadmap = json.loads((ROOT / ".github" / "roadmap.json").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = set(re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE))
    assert released, "CHANGELOG.md has no released versions"

    missing = [
        (item["id"], item.get("shipped_in"))
        for item in roadmap["items"]
        if item.get("status") == "shipped" and item.get("shipped_in") not in released
    ]
    assert not missing, f"roadmap claims shipments the changelog does not record: {missing}"


def test_every_queued_item_is_contributor_ready() -> None:
    """An item without files or acceptance criteria is a wish, not a task."""
    roadmap = json.loads((ROOT / ".github" / "roadmap.json").read_text(encoding="utf-8"))
    incomplete = [
        item["id"]
        for item in roadmap["items"]
        if item.get("status") != "shipped"
        and (not item.get("files") or not item.get("acceptance_criteria"))
    ]
    assert not incomplete, f"queued items missing files/criteria: {incomplete}"


def test_closing_shipped_issues_frees_queue_slots_immediately() -> None:
    """A closure must count as a freed slot in the same run, not the next one."""
    roadmap = {
        "max_open_roadmap_issues": 2,
        "items": [
            {
                "id": "RM-001",
                "title": "done",
                "summary": "s",
                "motivation": "m",
                "status": "shipped",
            },
            {"id": "RM-002", "title": "next", "summary": "s", "motivation": "m"},
        ],
    }
    issues = [
        {"number": 1, "state": "open", "body": "<!-- roadmap-id: RM-001 -->"},
        {"number": 2, "state": "open", "body": "<!-- roadmap-id: RM-999 -->"},
    ]
    to_close = bot_mod.select_shipped_issues_to_close(roadmap, issues)
    assert [number for number, _ in to_close] == [1]

    closed = {number for number, _ in to_close}
    remaining = [i for i in issues if i["number"] not in closed]
    opened = bot_mod.select_items_to_open(roadmap, remaining)
    assert [item["id"] for item in opened] == ["RM-002"]


def _issue(number: int, item_id: str, labels: list[str] | None = None) -> dict:
    return {
        "number": number,
        "state": "open",
        "body": f"<!-- roadmap-id: {item_id} -->",
        "labels": [{"name": name} for name in (labels or [])],
    }


class TestStarterFloor:
    """The queue has two budgets: a ceiling for the maintainer, a floor for newcomers."""

    def _roadmap(self) -> dict:
        return {
            "max_open_roadmap_issues": 2,
            "min_open_good_first_issues": 2,
            "items": [
                {
                    "id": "RM-001",
                    "title": "easy",
                    "summary": "s",
                    "motivation": "m",
                    "labels": ["good first issue"],
                },
                {
                    "id": "RM-002",
                    "title": "hard",
                    "summary": "s",
                    "motivation": "m",
                    "labels": ["difficulty/hard"],
                },
                {
                    "id": "RM-003",
                    "title": "easy2",
                    "summary": "s",
                    "motivation": "m",
                    "labels": ["good first issue"],
                },
            ],
        }

    def test_full_queue_still_admits_a_starter(self) -> None:
        # Queue is at its ceiling (2 open) and neither open issue is beginner-friendly.
        existing = [
            _issue(1, "RM-900", ["difficulty/hard"]),
            _issue(2, "RM-901", ["difficulty/hard"]),
        ]
        opened = bot_mod.select_items_to_open(self._roadmap(), existing)
        assert [item["id"] for item in opened] == ["RM-001", "RM-003"]

    def test_floor_already_met_means_ceiling_applies(self) -> None:
        existing = [
            _issue(1, "RM-900", ["good first issue"]),
            _issue(2, "RM-901", ["good first issue"]),
        ]
        assert bot_mod.select_items_to_open(self._roadmap(), existing) == []

    def test_starter_is_preferred_over_harder_work_when_both_fit(self) -> None:
        roadmap = self._roadmap()
        roadmap["max_open_roadmap_issues"] = 3
        roadmap["min_open_good_first_issues"] = 1
        opened = bot_mod.select_items_to_open(roadmap, [])
        assert opened[0]["labels"] == ["good first issue"]

    def test_count_open_starters_ignores_closed_and_ships(self) -> None:
        issues = [
            _issue(1, "RM-001", ["good first issue"]),
            {**_issue(2, "RM-002", ["good first issue"]), "state": "closed"},
            {"number": 3, "state": "open", "body": "x", "labels": [], "pull_request": {"url": "u"}},
        ]
        assert bot_mod.count_open_starters(issues) == 1
