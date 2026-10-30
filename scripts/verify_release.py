#!/usr/bin/env python3
"""Prove a release is releasable *before* anything is uploaded.

Issue #50 is not really "add a workflow" - the workflow existed. It is "the chain from a git
tag to `pip install vexar-mcp` has never been proven, so every link is still a guess". This
script makes each link checkable in one second, offline, with no PyPI account:

* the version in ``pyproject.toml`` equals ``mcp_doctor.__version__`` equals the release tag
  (when one is given) and has a dated ``CHANGELOG.md`` section;
* the distribution name still normalizes to ``vexar-mcp`` - a rename that only reaches the
  workflow is a publish to the wrong project;
* the publish workflow still authenticates with OIDC (``id-token: write``) and does not shell
  out to ``twine upload``, which cannot consume an OIDC token. That is the exact bug this
  repository shipped once; it is cheap to ship it again;
* the release workflow hands off to the publish workflow explicitly, because a GitHub Release
  authored by ``GITHUB_TOKEN`` does not start other workflows.

Usage:
    python scripts/verify_release.py                  # verify the working tree
    python scripts/verify_release.py --tag v0.2.7     # also require the tag to agree
    python scripts/verify_release.py --pypi           # also ask PyPI what it already has

Exit code 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]

#: The distribution name users type into pip. Renaming the project renames this, and the
#: trusted publisher registration on PyPI is bound to it, so drift here breaks publishing.
EXPECTED_DISTRIBUTION = "vexar-mcp"

PUBLISH_WORKFLOW = Path(".github/workflows/publish-pypi.yml")
RELEASE_WORKFLOW = Path(".github/workflows/release.yml")

#: Hosts PyPI serves the JSON API from; only used by ``--pypi``.
PYPI_JSON = "https://pypi.org/pypi/{name}/json"

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
#: A released changelog heading, e.g. ``## [0.2.6] - 2026-10-28``.
CHANGELOG_HEADING_RE = re.compile(
    r"^##\s+\[(?P<version>[^\]]+)\]\s+-\s+(?P<date>\d{4}-\d{2}-\d{2})", re.M
)


def normalize(name: str) -> str:
    """Apply PEP 503 normalization, so ``Vexar_MCP`` and ``vexar-mcp`` compare equal."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def read_pyproject(root: Path) -> dict[str, Any]:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def package_version(root: Path) -> str:
    """Read ``__version__`` without importing the package (imports have side effects)."""
    text = (root / "src" / "mcp_doctor" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if match is None:
        raise SystemExit("verify_release: src/mcp_doctor/__init__.py declares no __version__")
    return match.group(1)


def check_versions(root: Path, tag: str | None) -> list[str]:
    """Every place that states the version must state the same version."""
    problems: list[str] = []
    project = read_pyproject(root)["project"]
    pyproject_version = str(project["version"])
    init_version = package_version(root)

    for label, value in (("pyproject.toml", pyproject_version), ("__init__.py", init_version)):
        if not VERSION_RE.match(value):
            problems.append(f"{label}: version '{value}' is not a MAJOR.MINOR.PATCH release")
    if pyproject_version != init_version:
        problems.append(
            f"pyproject.toml says {pyproject_version} but src/mcp_doctor/__init__.py says "
            f"{init_version}; one of them is stale"
        )

    if tag is not None:
        tag_version = tag.lstrip("v")
        if tag_version != pyproject_version:
            problems.append(
                f"release tag v{tag_version} does not match the packaged version "
                f"{pyproject_version}; the tag is what PyPI shows and what users pin, so "
                f"publishing would advertise a version the code does not claim"
            )
    return problems


def check_distribution_name(root: Path) -> list[str]:
    name = str(read_pyproject(root)["project"]["name"])
    if normalize(name) != EXPECTED_DISTRIBUTION:
        return [
            f"pyproject.toml distributes '{name}' (normalizes to '{normalize(name)}') but the "
            f"trusted publisher is registered for '{EXPECTED_DISTRIBUTION}'"
        ]
    return []


def check_changelog(root: Path, version: str) -> list[str]:
    """A release without a changelog entry is a release nobody can read about."""
    path = root / "CHANGELOG.md"
    if not path.is_file():
        return ["CHANGELOG.md is missing"]
    text = path.read_text(encoding="utf-8")
    headings = {m.group("version"): m.group("date") for m in CHANGELOG_HEADING_RE.finditer(text)}
    if version not in headings:
        return [
            f"CHANGELOG.md has no '## [{version}] - YYYY-MM-DD' section; move the Unreleased "
            f"notes under a dated heading for {version}"
        ]
    return []


def check_publish_workflow(root: Path) -> list[str]:
    """The OIDC contract with PyPI, asserted against the file that actually runs."""
    path = root / PUBLISH_WORKFLOW
    if not path.is_file():
        return [f"{PUBLISH_WORKFLOW.as_posix()} is missing"]
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    # Matches the command, not prose: a step may write `run: python -m twine upload`
    # inline or inside a block scalar, and may also *mention* twine upload in a comment.
    if re.search(r"""(?<![\w`])(?:python\s+-m\s+)?twine\s+upload\b""", text):
        problems.append(
            f"{PUBLISH_WORKFLOW.name}: runs 'twine upload', which cannot use an OIDC token. "
            f"Use pypa/gh-action-pypi-publish, or supply a token."
        )
    if "id-token: write" not in text:
        problems.append(
            f"{PUBLISH_WORKFLOW.name}: no 'id-token: write' permission, so Trusted Publishing "
            f"cannot mint an upload token"
        )
    if "pypa/gh-action-pypi-publish@" not in text:
        problems.append(f"{PUBLISH_WORKFLOW.name}: does not use pypa/gh-action-pypi-publish")
    if not re.search(r"^\s+name:\s*pypi\s*$", text, re.M):
        problems.append(
            f"{PUBLISH_WORKFLOW.name}: no 'environment: name: pypi'. The environment name is "
            f"part of the trusted publisher registration on PyPI"
        )
    if "workflow_call" not in text:
        problems.append(f"{PUBLISH_WORKFLOW.name}: not callable, so release.yml cannot chain to it")
    return problems


def check_release_chain(root: Path) -> list[str]:
    """A tag push must reach PyPI without a human, and without a secret.

    ``release.yml`` creates the GitHub Release with ``GITHUB_TOKEN``. GitHub does not run
    workflows for events caused by ``GITHUB_TOKEN``, so the ``release: published`` trigger in
    the publish workflow never fires for a tag push. Unless release.yml calls it directly,
    tagging publishes nothing.
    """
    path = root / RELEASE_WORKFLOW
    if not path.is_file():
        return [f"{RELEASE_WORKFLOW.as_posix()} is missing"]
    text = path.read_text(encoding="utf-8")
    if "./.github/workflows/publish-pypi.yml" not in text:
        return [
            f"{RELEASE_WORKFLOW.name} does not call ./.github/workflows/publish-pypi.yml. The "
            f"release it creates is authored by GITHUB_TOKEN, which does not trigger the "
            f"publish workflow's 'release: published' event, so a tag push publishes nothing"
        ]
    return []


def check_pypi(root: Path, timeout: float = 10.0) -> list[str]:
    """Ask PyPI what it already has. Offline by default; ``--pypi`` opts in.

    A brand-new project cannot have a trusted publisher added to it - there is no project page
    to add it to - so this distinguishes "register a pending publisher" from "edit an existing
    project's publisher", which is the step people get stuck on.
    """
    name = normalize(str(read_pyproject(root)["project"]["name"]))
    version = package_version(root)
    try:
        with urllib.request.urlopen(  # noqa: S310 - fixed https URL
            PYPI_JSON.format(name=name), timeout=timeout
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return [
                f"PyPI has no project '{name}' yet: register a *pending* trusted publisher at "
                f"https://pypi.org/manage/account/publishing/ (owner RANJITHROSAN17, repo "
                f"Vexar-MCP, workflow publish-pypi.yml, environment pypi), then release"
            ]
        return [f"PyPI returned HTTP {exc.code} for '{name}'"]
    except (urllib.error.URLError, TimeoutError) as exc:
        return [f"could not reach PyPI ({exc}); re-run without --pypi to stay offline"]

    releases = payload.get("releases") or {}
    if version in releases:
        return [
            f"PyPI already serves '{name}' {version}; a version can never be uploaded twice, so "
            f"bump the version (and the tag) instead of re-releasing {version}"
        ]
    return []


def collect(root: Path, tag: str | None = None, pypi: bool = False) -> list[str]:
    """Run every offline check, plus the PyPI check when asked."""
    version = str(read_pyproject(root)["project"]["version"])
    problems = [
        *check_versions(root, tag),
        *check_distribution_name(root),
        *check_changelog(root, version),
        *check_publish_workflow(root),
        *check_release_chain(root),
    ]
    if pypi:
        problems.extend(check_pypi(root))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tag", help="release tag, e.g. v0.2.7; must match the packaged version")
    parser.add_argument("--pypi", action="store_true", help="also query PyPI (needs network)")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root to verify")
    parser.add_argument("--quiet", action="store_true", help="print nothing on success")
    args = parser.parse_args(argv)

    problems = collect(args.root, tag=args.tag, pypi=args.pypi)
    if problems:
        print(f"verify_release: {len(problems)} problem(s) found\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if not args.quiet:
        subject = f"v{args.tag.lstrip('v')}" if args.tag else "the working tree"
        print(f"verify_release: {subject} is releasable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
