#!/usr/bin/env python3
"""Validate every workflow and action manifest in the repository.

GitHub reports an invalid workflow as a *failed run with no jobs*, which looks like a bot
malfunction rather than a syntax error - and stays red on every push until someone reads
the file. This script fails CI instead, with a message that names the file and the field.

It checks:

* the file parses as YAML at all (an unquoted ``: `` in a step name breaks this);
* every ``permissions`` scope is a real GitHub scope (``pull_requests`` is not -
  ``pull-requests`` is);
* every job declares ``runs-on`` and at least one step;
* every ``uses:`` reference is either a local action or pinned to a ref with a version.

Usage:
    python .github/scripts/validate_workflows.py [--quiet]

Exit code 0 when everything is valid, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - PyYAML is a dev dependency
    print("validate_workflows: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    raise SystemExit(2) from None

ROOT = Path(__file__).resolve().parents[2]

# https://docs.github.com/actions/reference/workflows-and-actions/workflow-syntax#permissions
VALID_PERMISSIONS = frozenset(
    {
        "actions",
        "attestations",
        "checks",
        "contents",
        "deployments",
        "discussions",
        "id-token",
        "issues",
        "models",
        "packages",
        "pages",
        "pull-requests",
        "repository-projects",
        "security-events",
        "statuses",
    }
)

ACTIONS_DIR = ROOT / ".github" / "workflows"


def _check_permissions(scope: Any, where: str, errors: list[str]) -> None:
    if isinstance(scope, str):
        if scope not in {"read-all", "write-all"}:
            errors.append(f"{where}: '{scope}' is not a valid permission preset")
        return
    if isinstance(scope, dict):
        for key in scope:
            if key not in VALID_PERMISSIONS:
                hint = ""
                if "_" in str(key):
                    hint = f" (did you mean '{str(key).replace('_', '-')}'?)"
                errors.append(f"{where}: unknown permission scope '{key}'{hint}")


def _check_uses(uses: str, where: str, errors: list[str]) -> None:
    if uses.startswith("./"):
        return
    if "@" not in uses:
        errors.append(f"{where}: '{uses}' is not pinned to a ref")
        return
    ref = uses.rsplit("@", 1)[1]
    if ref in {"main", "master"}:
        errors.append(f"{where}: '{uses}' tracks a moving branch; pin a tag or SHA")


def validate_workflow(path: Path) -> list[str]:
    """Return a list of problems found in one workflow file."""
    errors: list[str] = []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        return [f"{path.name}: YAML syntax error{location}: {getattr(exc, 'problem', exc)}"]

    if not isinstance(doc, dict):
        return [f"{path.name}: workflow must be a mapping"]

    if "jobs" not in doc or not isinstance(doc["jobs"], dict):
        return [f"{path.name}: workflow has no 'jobs' mapping"]
    if not doc["jobs"]:
        return [f"{path.name}: workflow defines no jobs"]

    _check_permissions(doc.get("permissions"), f"{path.name}: workflow permissions", errors)

    for job_name, job in doc["jobs"].items():
        where = f"{path.name}: job '{job_name}'"
        if not isinstance(job, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        if "uses" in job:
            _check_uses(str(job["uses"]), where, errors)
            continue
        if "runs-on" not in job:
            errors.append(f"{where}: missing 'runs-on'")
        _check_permissions(job.get("permissions"), f"{where} permissions", errors)

        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{where}: has no steps")
            continue
        for index, step in enumerate(steps):
            step_where = f"{where} step {index + 1}"
            if not isinstance(step, dict):
                errors.append(f"{step_where}: must be a mapping")
                continue
            if "uses" in step:
                _check_uses(str(step["uses"]), step_where, errors)
            elif "run" not in step:
                errors.append(f"{step_where}: needs either 'uses' or 'run'")
    return errors


def validate_action(path: Path) -> list[str]:
    """Return a list of problems found in one composite action manifest."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        return [f"{path.name}: YAML syntax error{location}: {getattr(exc, 'problem', exc)}"]

    errors: list[str] = []
    if not isinstance(doc, dict):
        return [f"{path.name}: action manifest must be a mapping"]
    for field in ("name", "description", "runs"):
        if field not in doc:
            errors.append(f"{path.name}: missing required field '{field}'")
    runs = doc.get("runs")
    if isinstance(runs, dict):
        if runs.get("using") != "composite":
            errors.append(f"{path.name}: expected 'runs.using: composite'")
        steps = runs.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{path.name}: 'runs.steps' is empty")
        else:
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    errors.append(f"{path.name}: step {index + 1} must be a mapping")
                elif "uses" not in step and "run" not in step:
                    errors.append(f"{path.name}: step {index + 1} needs 'uses' or 'run'")
    return errors


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    errors: list[str] = []

    workflow_files = sorted(p for p in ACTIONS_DIR.glob("*.y*ml") if p.is_file())
    if not workflow_files:
        errors.append("no workflow files found")

    for path in workflow_files:
        errors.extend(validate_workflow(path))

    for candidate in (ROOT / "action.yml", ROOT / "action.yaml"):
        if candidate.is_file():
            errors.extend(validate_action(candidate))

    if errors:
        print(f"validate_workflows: {len(errors)} problem(s) found\n", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    if not quiet:
        print(f"validate_workflows: {len(workflow_files)} workflow file(s) valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
