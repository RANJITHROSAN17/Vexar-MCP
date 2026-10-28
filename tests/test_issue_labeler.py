"""Tests for the issue keyword labeler.

The labeler is deliberately narrow: it applies ``area/*`` and ``type/*`` from the issue text
and refuses to guess ``difficulty/*`` or ``good first issue``. These tests pin both halves of
that contract, because "helpful bot" and "bot that mislabels a beginner issue" differ only in
where the line is drawn.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "auto_label_issue.py"

spec = importlib.util.spec_from_file_location("auto_label_issue", SCRIPT)
assert spec is not None and spec.loader is not None
labeler = importlib.util.module_from_spec(spec)
sys.modules["auto_label_issue"] = labeler
spec.loader.exec_module(labeler)


class TestClassify:
    def test_parser_issue_gets_area_and_type(self) -> None:
        labels = labeler.classify(
            "TypeScript loader misses tools registered via a variable",
            "The parser returns no tools when the name is a constant. See "
            "src/mcp_doctor/loaders/typescript_loader.py.",
        )
        assert "area/parsers" in labels
        assert "type/bug" in labels
        assert len(labels) <= labeler.MAX_LABELS

    def test_rule_false_positive_is_typed_as_bug_not_security(self) -> None:
        labels = labeler.classify(
            "MCP007 false positive on tempfile paths",
            "The rule fires on `tempfile.NamedTemporaryFile()`; this is incorrect.",
        )
        assert "area/rules" in labels
        assert "type/bug" in labels

    def test_security_vocabulary_wins_over_feature(self) -> None:
        labels = labeler.classify(
            "Support detecting SQL injection through string formatting",
            "Currently a string-built query reaches `.execute()` unnoticed. This is a "
            "security gap.",
        )
        assert "type/security" in labels

    def test_docs_only_issue(self) -> None:
        labels = labeler.classify("Typo in docs/usage.md", "The --emit flag is misspelled.")
        assert "area/docs" in labels

    def test_empty_text_yields_nothing(self) -> None:
        assert labeler.classify("", "") == []
        assert labeler.classify("   \n  ", "") == []

    def test_no_vocabulary_match_yields_nothing_rather_than_noise(self) -> None:
        assert labeler.classify("Question about roadmap", "How do I read the queue?") == []

    def test_never_auto_applies_human_judgement_labels(self) -> None:
        # An issue that literally asks for difficulty/good-first labelling must not get it
        # from the bot: that decision is what makes a starter issue trustworthy.
        labels = labeler.classify(
            "good first issue: add MCP015 rule",
            "This is difficulty/easy and help wanted, a new rule for parameter docs.",
        )
        for forbidden in labeler.NEVER_AUTO_APPLIED:
            assert forbidden not in labels

    def test_output_is_bounded_and_deterministic(self) -> None:
        text = (
            "CLI command rule parser report workflow documentation security crash slow "
            "support add typo sarif html yaml ast"
        )
        first = labeler.classify(text, "")
        assert first == labeler.classify(text, "")
        assert len(first) <= labeler.MAX_LABELS

    def test_area_labels_are_preferred_over_type_labels_when_truncated(self) -> None:
        labels = labeler.classify(
            "feature: rule support for yaml manifests",
            "src/mcp_doctor/rules/ and docs/ mention a new rule, add it.",
        )
        assert labels[0].startswith("area/")


class TestApply:
    def test_existing_labels_are_never_removed(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        calls: list[tuple[str, str, object]] = []

        class Fake:
            def request(self, method: str, path: str, payload: object = None) -> object:
                calls.append((method, path, payload))
                if method == "GET":
                    return {"labels": [{"name": "help wanted"}, {"name": "area/rules"}]}
                return None

        added = labeler.GitHubClient.apply_labels(Fake(), 7, ["area/rules", "type/bug"])  # type: ignore[arg-type]
        assert added == ["type/bug"]
        assert ("POST", "/issues/7/labels", {"labels": ["type/bug"]}) in calls

    def test_no_post_when_everything_already_present(self) -> None:
        class Fake:
            def request(self, method: str, path: str, payload: object = None) -> object:
                assert method == "GET", "should not POST when nothing is new"
                return {"labels": [{"name": "area/rules"}]}

        assert labeler.GitHubClient.apply_labels(Fake(), 8, ["area/rules"]) == []  # type: ignore[arg-type]


class TestCli:
    def _event(self, tmp_path: Path, **issue: object) -> Path:
        payload = {"issue": {"number": 11, **issue}}
        path = tmp_path / "event.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_dry_run_prints_labels(self, tmp_path: Path, capsys: object) -> None:  # type: ignore[no-untyped-def]
        event = self._event(tmp_path, title="Parser crash on YAML manifest", body="traceback")
        assert labeler.main(["--event", str(event), "--dry-run"]) == 0
        out = capsys.readouterr().out  # type: ignore[attr-defined]
        assert "#11" in out and "area/parsers" in out

    def test_missing_event_is_a_usage_error(self, tmp_path: Path) -> None:
        assert labeler.main(["--event", str(tmp_path / "nope.json"), "--dry-run"]) == 2

    def test_pull_request_events_are_left_to_pr_governance(
        self, tmp_path: Path, capsys: object
    ) -> None:  # type: ignore[no-untyped-def]
        event = self._event(tmp_path, title="add parser", pull_request={"url": "x"})
        assert labeler.main(["--event", str(event), "--dry-run"]) == 0
        assert "pr-governance" in capsys.readouterr().out  # type: ignore[attr-defined]


class TestNoiseControl:
    """Real issue text from this repo, pinned so over-labelling cannot creep back in.

    An earlier revision applied four labels to the first example below, including
    ``area/docs`` and ``area/cli``, because the words "docs" and "command" appear in passing.
    A label that fires on every issue routes nothing.
    """

    REAL_RULE_ISSUE = (
        "feat(rules): add MCP015 rule when tool parameters have no description",
        "## Summary\n\nFlag tool parameters that carry no description. MCP001 covers the tool "
        "description; parameter-level docs are unchecked.\n\n## Proposed Files to Touch\n\n"
        "- `src/mcp_doctor/loaders/python_ast.py`\n- `src/mcp_doctor/rules/schema_rules.py`\n\n"
        "## Local Verification\n\n```bash\npython -m pytest tests/test_rules.py\n```",
    )

    def test_rule_issue_is_labelled_rules_and_parsers_only(self) -> None:
        labels = labeler.classify(*self.REAL_RULE_ISSUE)
        assert set(labels) == {"area/rules", "area/parsers"}

    def test_generic_word_alone_does_not_label_an_area(self) -> None:
        # "command" and "workflow" are weak signals; on their own they must not route an issue.
        assert labeler.classify("A command", "Some workflow ran.") == []

    def test_at_most_two_areas_and_two_types(self) -> None:
        labels = labeler.classify(
            "security: rule parser reporter cli workflow docs",
            "src/mcp_doctor/rules/ src/mcp_doctor/loaders/ src/mcp_doctor/reporters/ "
            "docs/ .github/workflows/ traceback injection slow add support",
        )
        assert sum(label.startswith("area/") for label in labels) <= labeler.MAX_AREA_LABELS
        assert sum(label.startswith("type/") for label in labels) <= labeler.MAX_TYPE_LABELS
