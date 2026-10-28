#!/usr/bin/env python3
"""Roadmap Status Bot - tells the repository what the next step is.

The curator bot asks *is there an issue for this?*. This one answers the question that
actually stalls a project on a quiet week: **what is the single next thing worth doing, and
is the plan still true?**

It maintains exactly one issue, found by marker and edited in place, so a weekly run adds no
notification noise. Three kinds of drift are reported, because each one is invisible without
something looking for it:

* an open issue whose roadmap item has already shipped - the queue is advertising dead work;
* a roadmap item naming a file that no longer exists - the plan has outlived the code;
* the package version disagreeing with the newest tag - a release that was never cut.

Usage:
    python .github/scripts/roadmap_status.py --dry-run
    python .github/scripts/roadmap_status.py --out status.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

STATUS_MARKER = "<!-- roadmap-status -->"
ROADMAP_MARKER_RE = re.compile(r"<!--\s*roadmap-id:\s*([A-Za-z0-9_-]+)\s*-->")
PRIORITY_RE = re.compile(r"priority/p(\d)")
DEFAULT_PLAN_DOC = "ROADMAP.md"


def _priority(item: dict[str, Any], index: int) -> tuple[int, int]:
    """Sort key: explicit `priority/pN` label first, manifest order as the tie-break."""
    for label in item.get("labels", []):
        name = label.get("name") if isinstance(label, dict) else label
        match = PRIORITY_RE.fullmatch(str(name))
        if match:
            return (int(match.group(1)), index)
    return (5, index)


def queued_items(roadmap: dict[str, Any]) -> list[dict[str, Any]]:
    """Return unshipped items in the order they should be picked up."""
    items = [
        (index, item)
        for index, item in enumerate(roadmap.get("items", []))
        if item.get("status") != "shipped"
    ]
    items.sort(key=lambda pair: _priority(pair[1], pair[0]))
    return [item for _, item in items]


def open_item_ids(issues: list[dict[str, Any]]) -> set[str]:
    """Return roadmap ids that already have an open issue."""
    ids: set[str] = set()
    for issue in issues:
        if "pull_request" in issue or issue.get("state") != "open":
            continue
        match = ROADMAP_MARKER_RE.search(issue.get("body") or "")
        if match:
            ids.add(match.group(1))
    return ids


def pick_next(
    roadmap: dict[str, Any], issues: list[dict[str, Any]], limit: int = 3
) -> list[dict[str, Any]]:
    """Return the next items worth doing that are *not* already on the board.

    Already-open items are excluded because telling someone to start work that already has an
    owner is how a status page loses its readers.
    """
    in_flight = open_item_ids(issues)
    return [item for item in queued_items(roadmap) if item["id"] not in in_flight][:limit]


def in_flight_items(roadmap: dict[str, Any], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return queued items that already have an open issue, in pick order."""
    open_ids = open_item_ids(issues)
    return [item for item in queued_items(roadmap) if item["id"] in open_ids]


def shipped_items(roadmap: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in roadmap.get("items", []) if item.get("status") == "shipped"]


def file_drift(roadmap: dict[str, Any], root: Path) -> list[str]:
    """Return items that name a file or directory which is no longer in the tree.

    A plan that points at files that do not exist reads as authoritative and is wrong, which
    is worse than a plan that is obviously out of date. Paths are matched leniently: a
    directory, a glob-free path, or a path whose parent exists all count as present.
    """
    problems: list[str] = []
    for item in queued_items(roadmap):
        for path in item.get("files") or []:
            candidate = path.rstrip("/")
            # A trailing slash declares a directory the item will *create*; that is a plan,
            # not drift, so only concrete file paths are checked.
            if path.endswith("/") or any(char in candidate for char in "*?["):
                continue
            target = root / candidate
            if target.exists() or (target.parent).exists():
                continue
            problems.append(f"`{item['id']}` names `{path}`, which is not in the tree")
    return problems


def items_without_criteria(roadmap: dict[str, Any]) -> list[str]:
    """Return queued items with no acceptance criteria - an issue nobody can agree is done."""
    return [item["id"] for item in queued_items(roadmap) if not item.get("acceptance_criteria")]


def package_version(root: Path) -> str | None:
    """Read the version from pyproject.toml without requiring tomllib on Python 3.10."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    in_project = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project:
            match = re.fullmatch(r'version\s*=\s*"([^"]+)"', stripped)
            if match:
                return match.group(1)
    return None


def latest_tag(root: Path) -> str | None:
    """Return the newest reachable tag, or None when git is unavailable or has no tags."""
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    tag = out.stdout.strip()
    return tag or None


def version_drift(version: str | None, tag: str | None) -> str | None:
    """Return a warning when the declared version and the newest tag disagree."""
    if not version or not tag:
        return None
    if tag.lstrip("v") == version.lstrip("v"):
        return None
    return (
        f"`pyproject.toml` says **{version}** but the newest tag is **{tag}** - "
        f"a release was bumped and never cut, or a tag was cut from a different version."
    )


def _bar(done: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "`" + "-" * width + "` 0/0"
    filled = round(width * done / total)
    return "`" + "#" * filled + "." * (width - filled) + f"` {done}/{total}"


def _label(item: dict[str, Any], prefix: str) -> str:
    for label in item.get("labels", []):
        name = label.get("name") if isinstance(label, dict) else label
        if str(name).startswith(prefix):
            return str(name).split("/", 1)[1]
    return "-"


def render_status(
    roadmap: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    root: Path,
    version: str | None = None,
    tag: str | None = None,
) -> str:
    """Render the single status issue body."""
    shipped = shipped_items(roadmap)
    total = len(roadmap.get("items", []))
    next_up = pick_next(roadmap, issues)
    flight = in_flight_items(roadmap, issues)

    lines: list[str] = [
        STATUS_MARKER,
        "# Roadmap status",
        "",
        f"Progress: {_bar(len(shipped), total)} items shipped.",
        "",
    ]
    if not issues:
        lines.append(
            "_No open issues were read for this rendering, so every item below looks "
            "unclaimed. A live run fetches them first._"
        )
        lines.append("")

    lines.append("## Next up")
    lines.append("")
    if next_up:
        lines.append("| | Item | Difficulty | First acceptance criterion |")
        lines.append("|---|---|---|---|")
        for item in next_up:
            criteria = (item.get("acceptance_criteria") or ["-"])[0]
            lines.append(
                f"| `{item['id']}` | {item['title']} | {_label(item, 'difficulty/')} | {criteria} |"
            )
        lines.append("")
        plan_doc = roadmap.get("plan_doc", DEFAULT_PLAN_DOC)
        lines.append(
            "The full plan, with the verification that produced this ordering, is "
            f"[`{plan_doc}`]({plan_doc}). The top row is the recommended next step; open the "
            "issue and the acceptance criteria are the definition of done."
        )
    else:
        lines.append("Nothing queued - either everything shipped or the manifest is stale.")
    lines.append("")

    lines.append("## In flight")
    lines.append("")
    if flight:
        for item in flight:
            lines.append(f"- `{item['id']}` {item['title']}")
    else:
        lines.append("- None. The queue is empty, which costs contributors before it costs you.")
    lines.append("")

    shipped_lines = [
        f"- `{item['id']}` {item['title']} - {item.get('shipped_in', 'main')}" for item in shipped
    ]
    lines.append(f"<details><summary>Shipped ({len(shipped)})</summary>\n")
    lines.extend(shipped_lines or ["- None yet."])
    lines.extend(["", "</details>", ""])

    warnings: list[str] = []
    in_flight_ids = open_item_ids(issues)
    shipped_ids = {item["id"] for item in shipped}
    for item_id in sorted(in_flight_ids & shipped_ids):
        warnings.append(
            f"`{item_id}` shipped but its issue is still open - the curator will close it"
        )
    warnings.extend(file_drift(roadmap, root))
    drift = version_drift(version, tag)
    if drift:
        warnings.append(drift)
    missing = items_without_criteria(roadmap)
    if missing:
        warnings.append(f"no acceptance criteria: {', '.join(missing)}")

    lines.append("## Drift")
    lines.append("")
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- None. The plan, the queue and the tree agree.")
    lines.append("")
    lines.append(
        "<sub>Generated by `.github/scripts/roadmap_status.py`. This issue is edited in "
        "place, never reopened.</sub>"
    )
    return "\n".join(lines)


def github(path: str, token: str, payload: Any = None, method: str = "GET") -> Any:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "vexar-roadmap-status-bot",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish the roadmap status issue.")
    parser.add_argument("--roadmap", type=Path, default=Path(".github/roadmap.json"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, default=None, help="Write the markdown here too.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the status and exit without calling the API.",
    )
    args = parser.parse_args(argv)

    roadmap = json.loads(args.roadmap.read_text(encoding="utf-8"))
    issues: list[dict[str, Any]] = []
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")

    if not args.dry_run:
        if not repo or not token:
            print("Error: GITHUB_REPOSITORY and GITHUB_TOKEN are required.", file=sys.stderr)
            return 2
        page = 1
        while True:
            batch = github(f"/repos/{repo}/issues?state=open&per_page=100&page={page}", token)
            if not batch:
                break
            issues.extend(batch)
            if len(batch) < 100:
                break
            page += 1

    body = render_status(
        roadmap,
        issues,
        root=args.root,
        version=package_version(args.root),
        tag=latest_tag(args.root),
    )

    if args.out:
        args.out.write_text(body + "\n", encoding="utf-8")

    if args.dry_run:
        print(body)
        return 0

    existing = github(f"/repos/{repo}/issues?state=open&per_page=100&labels=roadmap", token) or []
    target = next((issue for issue in existing if STATUS_MARKER in (issue.get("body") or "")), None)
    try:
        if target:
            github(
                f"/repos/{repo}/issues/{target['number']}",
                token,
                {"body": body},
                method="PATCH",
            )
            print(f"[roadmap-status] Updated #{target['number']}")
        else:
            created = github(
                f"/repos/{repo}/issues",
                token,
                {"title": "Roadmap status", "body": body, "labels": ["roadmap"]},
                method="POST",
            )
            print(f"[roadmap-status] Created #{created['number']}")
    except urllib.error.HTTPError as exc:
        print(f"GitHub API error ({exc.code}): {exc.read().decode('utf-8', 'replace')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
