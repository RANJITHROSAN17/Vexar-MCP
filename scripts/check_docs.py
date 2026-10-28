#!/usr/bin/env python3
"""Check every relative link in the repository's Markdown.

Onboarding documentation is the first thing a new user touches, and a broken link is the
cheapest possible way to lose them. This runs in CI so a moved file cannot silently break the
README, the quickstart, or the docs index.

Checks, in order of how often they actually catch something:

1. a relative link points at a path that exists (with GitHub's case-sensitivity);
2. an in-page or cross-page `#anchor` exists as a heading in the target file;
3. a `` `docs/foo.md` ``-style path written in backticks exists — these are how people
   accidentally reference files in prose, and they rot just as fast as links.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Directories that are not project documentation.
SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "build", "dist", "__pycache__", ".pytest_cache"}
)

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
#: Explicit HTML anchors, e.g. ``<a id="mcp001"></a>``. GitHub honours these, and they survive
#: heading renames, which is why the rule index uses them instead of heading slugs.
HTML_ANCHOR_RE = re.compile(r'<a\s+(?:id|name)="([^"]+)"')

BACKTICK_PATH_RE = re.compile(
    r"`((?:docs|src|tests|scripts|examples|assets)/[A-Za-z0-9_./-]+\.(?:md|py|yml|yaml|json|toml|svg|png))`"
)

#: Documents that are historical records rather than live instructions. They must quote paths
#: that no longer exist — a changelog that cannot name a removed file is not a changelog — so
#: only their real links are checked.
HISTORICAL_FILES = frozenset({"CHANGELOG.md"})

#: Links that are intentionally external or generated.
IGNORED_PREFIXES = ("http://", "https://", "mailto:", "tel:")


def slugify(heading: str) -> str:
    """Approximate GitHub's heading anchor rules closely enough to catch typos."""
    text = heading.strip().lower()
    text = re.sub(r"`", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # inline links -> their text
    text = re.sub(r"[^\w\s-]", "", text)
    # One hyphen per space, runs preserved: GitHub deletes an em dash and leaves the two
    # spaces on either side, so "Step 0 — what you need" anchors as "step-0--what-you-need".
    text = text.replace(" ", "-")
    return text.strip("-")


def anchors_of(path: Path) -> set[str]:
    """Return the set of anchors a Markdown file defines."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    # Ignore fenced code blocks: a '#' line inside one is not a heading.
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    anchors = {slugify(m.group(1)) for m in HEADING_RE.finditer(text)}
    anchors |= set(HTML_ANCHOR_RE.findall(text))
    return anchors


def markdown_files() -> list[Path]:
    """Every Markdown file that ships with the project."""
    files: list[Path] = []
    for path in ROOT.rglob("*.md"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def check_file(md: Path, root: Path) -> list[str]:
    """Return human-readable problems found in one Markdown file."""
    problems: list[str] = []
    text = md.read_text(encoding="utf-8")
    # Also strip fenced blocks so example links inside them are not resolved.
    body = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    rel_md = md.relative_to(root)

    for match in LINK_RE.finditer(body):
        target = match.group(1).strip()
        if target.startswith("#") and len(target) > 1:
            if target[1:] not in anchors_of(md):
                problems.append(f"{rel_md}: anchor '{target}' has no matching heading")
            continue
        if target.startswith(IGNORED_PREFIXES):
            continue
        if target.startswith("mailto:"):
            continue

        path_part, _, anchor = target.partition("#")
        path_part = path_part.replace("%20", " ")
        if not path_part:
            continue
        resolved = (md.parent / path_part).resolve()
        if not resolved.exists():
            problems.append(f"{rel_md}: link '{target}' points at a missing file")
            continue
        if anchor and resolved.suffix == ".md" and anchor not in anchors_of(resolved):
            problems.append(f"{rel_md}: link '{target}' anchors to a heading that does not exist")

    # A document explicitly marked historical is quoting the past, including files that were
    # planned and never created. Its real links are still checked above.
    historical = "Status: historical" in text or md.name in HISTORICAL_FILES
    for match in [] if historical else BACKTICK_PATH_RE.finditer(body):
        candidate = root / match.group(1)
        if not candidate.exists():
            problems.append(f"{rel_md}: references missing path `{match.group(1)}`")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check relative Markdown links.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    files = markdown_files() if args.root == ROOT else sorted(args.root.rglob("*.md"))
    problems: list[str] = []
    for md in files:
        problems.extend(check_file(md, args.root))

    if problems:
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            f"\n{len(problems)} documentation problem(s) across {len(files)} file(s)",
            file=sys.stderr,
        )
        return 1

    print(f"documentation links OK ({len(files)} Markdown file(s) checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
