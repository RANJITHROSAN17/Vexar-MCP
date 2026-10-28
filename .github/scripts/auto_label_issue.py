#!/usr/bin/env python3
"""Apply ``area/*`` and ``type/*`` labels to a new issue from its title and body.

Design rules, learned from the rest of this repository:

* **Facts only.** This maps *what the issue is about* (paths and vocabulary) to labels. It
  never applies ``difficulty/*`` or ``good first issue``: how hard a task is, and whether it
  is beginner-appropriate, is a maintainer judgement, and a bot that guesses it produces
  starter issues nobody can finish. That erodes trust faster than missing labels do.
* **Additive.** Existing labels are never removed or overwritten.
* **Bounded.** At most ``MAX_LABELS`` labels are applied, in a stable order, so a rambling
  issue cannot end up wearing twelve labels.
* **Zero dependencies, pure function.** ``classify`` takes text and returns label names, so
  it is unit-testable without a network or a token.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

#: Labels a human decides. Listed here so the exclusion is visible rather than incidental.
NEVER_AUTO_APPLIED = frozenset(
    {
        "good first issue",
        "help wanted",
        "difficulty/easy",
        "difficulty/medium",
        "difficulty/hard",
        "pinned",
        "roadmap",
    }
)

MAX_LABELS = 4

#: Scoring: a distinctive term is worth 2, a generic one 1, an explicit path 3. A label needs
#: ``THRESHOLD`` points. This exists because in a security scanner almost every issue mentions
#: "rule", so pure keyword presence labels everything ``area/rules`` -- which is the same as
#: labelling nothing.
STRONG = 2
WEAK = 1
PATH_HINT = 3
THRESHOLD = 2

MAX_AREA_LABELS = 2
MAX_TYPE_LABELS = 2

#: (area label, distinctive terms, generic terms)
_AREA_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "area/rules",
        ("mcp0", "false positive", "rule id", "new rule", "check fires", "no finding"),
        ("rule", "severity", "diagnostic", "category"),
    ),
    (
        "area/parsers",
        (
            "parser",
            "loader",
            "typescript",
            "pydantic",
            "basemodel",
            "typeddict",
            "tree-sitter",
            "python_ast",
        ),
        ("ast", "yaml", "manifest", "parse"),
    ),
    (
        "area/reporters",
        ("reporter", "sarif", "html report", "markdown report", "--format", "sparkline"),
        ("report", "output"),
    ),
    (
        "area/cli",
        ("subcommand", "argparse", "console script", "exit code", "--emit", "vexar-mcp init"),
        ("cli", "flag", "command"),
    ),
    (
        "area/ci",
        ("github actions", "action.yml", "composite action", "pre-commit", "workflow file"),
        ("workflow", "pipeline"),
    ),
    (
        "area/docs",
        ("docs/", "readme", "docstring", "changelog", "contributing.md"),
        ("documentation", "typo", "guide"),
    ),
)

#: (type label, distinctive terms, generic terms)
_TYPE_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "type/security",
        ("security", "vulnerab", "injection", "ssrf", "cve", "exploit", "credential leak"),
        ("secret", "unsafe", "privilege"),
    ),
    (
        "type/bug",
        (
            "traceback",
            "regression",
            "false positive",
            "not detected",
            "silently",
            "misses tool",
            "no tools found",
            "not parsed",
        ),
        ("bug", "crash", "incorrect", "broken", "fails", "misses", "wrong", "unexpected", "panic"),
    ),
    (
        "type/performance",
        ("performance", "quadratic", "o(n)", "memory usage", "latency"),
        ("slow", "speed", "memory", "optimi"),
    ),
    (
        "type/feature",
        ("new rule", "would be nice", "feature request", "add support"),
        ("add", "support", "implement", "feature", "proposal", "enhancement", "allow"),
    ),
)

#: Structural hints: a contributor pasted a real path, which is the strongest signal there is.
_PATH_HINTS: tuple[tuple[str, str], ...] = (
    ("src/mcp_doctor/rules/", "area/rules"),
    ("src/mcp_doctor/loaders/", "area/parsers"),
    ("src/mcp_doctor/reporters/", "area/reporters"),
    ("src/mcp_doctor/cli.py", "area/cli"),
    ("src/mcp_doctor/config.py", "area/cli"),
    (".github/workflows/", "area/ci"),
    ("action.yml", "area/ci"),
    ("docs/", "area/docs"),
)


def _score(
    text: str,
    rules: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...],
    path_hints: dict[str, int],
) -> list[tuple[int, int, str]]:
    """Return `(score, rule_order, label)` for every label that clears the threshold."""
    scored: list[tuple[int, int, str]] = []
    for order, (label, strong, weak) in enumerate(rules):
        score = path_hints.get(label, 0)
        score += STRONG * sum(1 for term in strong if term in text)
        score += WEAK * sum(1 for term in weak if term in text)
        if score >= THRESHOLD:
            scored.append((score, order, label))
    return scored


def classify(title: str, body: str = "") -> list[str]:
    """Return the labels this issue's text supports, strongest first.

    Deterministic: same text always yields the same list, so re-runs are idempotent. Areas are
    listed before types, and each group is capped, because a wall of labels routes nothing.
    """
    text = f"{title}\n{body}".lower()
    if not text.strip():
        return []

    path_hints: dict[str, int] = {}
    for needle, label in _PATH_HINTS:
        if needle in text:
            path_hints[label] = PATH_HINT

    areas = sorted(_score(text, _AREA_RULES, path_hints), key=lambda row: (-row[0], row[1]))
    types = sorted(_score(text, _TYPE_RULES, {}), key=lambda row: (-row[0], row[1]))

    labels = [label for _, _, label in areas[:MAX_AREA_LABELS]]
    for label in [label for _, _, label in types[:MAX_TYPE_LABELS]]:
        if label not in labels:
            labels.append(label)

    return [label for label in labels if label not in NEVER_AUTO_APPLIED][:MAX_LABELS]


class GitHubClient:
    """Minimal GitHub REST client; no third-party dependency."""

    def __init__(self, repo: str, token: str) -> None:
        self.base_url = f"https://api.github.com/repos/{repo}"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mcp-doctor-issue-labeler",
        }

    def request(self, method: str, path: str, payload: object = None) -> object:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=self.headers, method=method
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None

    def apply_labels(self, number: int, labels: list[str]) -> list[str]:
        """Add labels without removing existing ones. Returns what was actually added."""
        issue = self.request("GET", f"/issues/{number}") or {}
        existing = {
            entry["name"]
            for entry in (issue.get("labels", []) if isinstance(issue, dict) else [])
            if isinstance(entry, dict)
        }
        to_add = [label for label in labels if label not in existing]
        if to_add:
            self.request("POST", f"/issues/{number}/labels", {"labels": to_add})
        return to_add


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Label a new issue from its text.")
    parser.add_argument("--event", type=Path, default=None, help="Path to the event JSON.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--dry-run", action="store_true", help="Print labels, change nothing.")
    args = parser.parse_args(argv)

    event_path = args.event or os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not Path(event_path).is_file():
        print("error: no event JSON available", file=sys.stderr)
        return 2

    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    issue = event.get("issue") or {}
    if not issue.get("number"):
        print("error: event contains no issue", file=sys.stderr)
        return 2
    if "pull_request" in issue:
        print("[labeler] event is a pull request; pr-governance.yml owns PR labels")
        return 0

    labels = classify(issue.get("title") or "", issue.get("body") or "")
    if not labels:
        print("[labeler] no confident match; leaving the issue unlabelled for a human")
        return 0

    if args.dry_run:
        print(f"[dry-run] #{issue['number']} -> {', '.join(labels)}")
        return 0

    token = os.environ.get("GITHUB_TOKEN", "")
    if not args.repo or not token:
        print("error: GITHUB_REPOSITORY and GITHUB_TOKEN must be set", file=sys.stderr)
        return 2

    try:
        added = GitHubClient(args.repo, token).apply_labels(int(issue["number"]), labels)
    except urllib.error.HTTPError as exc:  # pragma: no cover - network failure path
        print(f"GitHub API error ({exc.code}): {exc.read().decode('utf-8', 'replace')}")
        return 1

    print(f"[labeler] #{issue['number']} added: {', '.join(added) or 'nothing new'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
