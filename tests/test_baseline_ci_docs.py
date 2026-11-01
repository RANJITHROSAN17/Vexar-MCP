"""Execute the actual documented YAML commands; do not duplicate their argv in tests."""

from __future__ import annotations

import json
import re
import shlex
import shutil
from pathlib import Path

import pytest
import yaml

from mcp_doctor.baseline import read_baseline
from mcp_doctor.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _workflow() -> dict:
    text = (ROOT / "docs/usage.md").read_text(encoding="utf-8")
    section = text.split("## Worked CI job: adopt once, gate only new findings", 1)[1]
    match = re.search(r"```yaml\n(.*?)```", section, re.S)
    assert match is not None
    return yaml.safe_load(match[1])  # type: ignore[no-any-return]


def _commands() -> tuple[list[str], list[str]]:
    steps = _workflow()["jobs"]["scan"]["steps"]
    commands = [shlex.split(step["run"]) for step in steps if "run" in step]
    install, adopt, enforce = commands
    # This setup command is already executed by CI on every test matrix entry.
    assert install == ["python", "-m", "pip", "install", "-e", ".[dev]"]
    assert 'python -m pip install -e ".[dev]"' in (ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )
    assert adopt[0] == enforce[0] == "vexar-mcp"
    return adopt[1:], enforce[1:]


def test_documented_adoption_then_unchanged_enforcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    adopt, enforce = _commands()
    assert main(enforce) == 2  # No committed baseline: fail closed.
    assert main(adopt) == 0
    baseline_path = Path(adopt[adopt.index("--write-baseline") + 1])
    baseline = read_baseline(baseline_path)
    assert baseline.policy == "ci"
    assert baseline.entries  # Intentional findings really were adopted.
    snapshot = baseline_path.read_bytes()
    assert main(enforce) == 0
    capsys.readouterr()
    # JSON is only instrumentation; the enforcing command above is executed unchanged.
    assert main([*enforce, "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["baseline"]["new"] == 0
    assert report["baseline"]["resolved"] == 0
    assert report["baseline"]["unchanged"] > 0
    assert baseline_path.read_bytes() == snapshot

    # A new file with existing bad code has new fingerprints and must fail the gate.
    shutil.copyfile("examples/vulnerable_server.py", "examples/new_server.py")
    assert main(enforce) == 1
    assert baseline_path.read_bytes() == snapshot


def test_workflow_separates_manual_adoption_from_enforcement() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["scan"]["steps"]
    adopt = next(step for step in steps if "--write-baseline" in step.get("run", ""))
    enforce = next(step for step in steps if "--baseline " in step.get("run", ""))
    assert adopt["if"] == "github.event_name == 'workflow_dispatch' && inputs.adopt == true"
    assert enforce["if"] == "github.event_name != 'workflow_dispatch' || inputs.adopt != true"
    artifact = next(step for step in steps if "upload-artifact" in step.get("uses", ""))
    assert artifact["if"] == adopt["if"]
    assert artifact["with"]["include-hidden-files"] is True
    assert artifact["with"]["if-no-files-found"] == "error"
    assert not any(step.get("continue-on-error") for step in steps)
