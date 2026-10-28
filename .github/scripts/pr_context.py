#!/usr/bin/env python3
"""Say which roadmap item a pull request advances, and what done still means.

Every bot that re-runs the test suite is noise: CI already ran it, and the reviewer can see
the red X. The one thing a comment can add that CI cannot is *context* - whether this change
is on-plan, which acceptance criteria it is supposed to satisfy, and which issue it claims to
close. That is what this script produces.

It refuses to guess. If no roadmap item matches the changed files, it says so instead of
picking the closest one, because a wrong map is worse than no map.

Usage:
    python .github/scripts/pr_context.py --changed changed_files.txt --body pr_body.md
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)


def _item_files(item: dict[str, Any]) -> list[str]:
    return [str(path).rstrip("/") for path in item.get("files") or []]


def match_items(
    roadmap: dict[str, Any], changed: list[str]
) -> list[tuple[dict[str, Any], list[str], int]]:
    """Return `(item, overlapping_paths, total_item_paths)` for items this PR touches.

    A file matches when the changed path is the item path, sits inside it (a directory), or
    shares its parent directory - the last rule catches a PR that adds `tests/test_report.py`
    for an item that names `src/.../report.py`, which is the same unit of work.
    """
    changed_set = {path.strip() for path in changed if path.strip()}
    changed_stems = {Path(path).stem.removeprefix("test_") for path in changed_set}
    matches: list[tuple[dict[str, Any], list[str], int]] = []
    for item in roadmap.get("items", []):
        if item.get("status") == "shipped":
            continue
        item_paths = _item_files(item)
        hits = [
            path
            for path in item_paths
            if any(
                hit == path
                or hit.startswith(path + "/")
                or hit.rsplit("/", 1)[0] == path
                # `tests/test_report.py` is the test for `.../report.py`: same unit of work.
                or Path(path).stem in changed_stems
                for hit in changed_set
            )
        ]
        if hits:
            matches.append((item, hits, len(item_paths)))
    matches.sort(key=lambda triple: (-len(triple[1]), triple[0]["id"]))
    return matches


def render_context(roadmap: dict[str, Any], changed: list[str], body: str) -> str:
    """Render the roadmap section of the PR comment."""
    lines: list[str] = ["### Roadmap context", ""]
    matches = match_items(roadmap, changed)

    if not matches:
        lines.append(
            "No roadmap item matches the files in this pull request. That is not a rejection -"
            " some work is not on the plan - but the description should say which problem this"
            " solves, or the change should be added to `.github/roadmap.json` so the plan stays"
            " the single description of what is being built."
        )
    else:
        top, hits, total = matches[0]
        lines.append(
            f"This PR touches **`{top['id']}` - {top['title']}** "
            f"({len(hits)} of its {total} named file(s): {', '.join(f'`{h}`' for h in hits)})."
        )
        lines.append("")
        lines.append("Definition of done, from the manifest:")
        lines.append("")
        lines.extend(f"- [ ] {criterion}" for criterion in top.get("acceptance_criteria") or [])
        if len(matches) > 1:
            others = ", ".join(f"`{item['id']}`" for item, _, _ in matches[1:])
            lines.append("")
            lines.append(f"It also overlaps {others} - say in the description if that is intended.")
    lines.append("")

    closes = sorted(set(CLOSES_RE.findall(body or "")))
    if closes:
        lines.append(
            f"Claims to close {', '.join('#' + number for number in closes)}. Verify the "
            "acceptance criteria are actually met before merging - a linked issue closes on "
            "merge whether or not the work is finished."
        )
    else:
        lines.append(
            "No `Closes #N` reference found. If this finishes a roadmap issue, add one so the "
            "issue closes with the merge rather than staying open and misleading the queue."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Map a PR's changed files to roadmap items.")
    parser.add_argument("--roadmap", type=Path, default=Path(".github/roadmap.json"))
    parser.add_argument("--changed", type=Path, required=True, help="File of changed paths.")
    parser.add_argument("--body", type=Path, default=None, help="File holding the PR body.")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    roadmap = json.loads(args.roadmap.read_text(encoding="utf-8"))
    changed = args.changed.read_text(encoding="utf-8").splitlines()
    body = args.body.read_text(encoding="utf-8") if args.body and args.body.is_file() else ""

    markdown = render_context(roadmap, changed, body)
    if args.out:
        args.out.write_text(markdown + "\n", encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
