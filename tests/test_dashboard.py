"""Pages artifact and scanner/dashboard contract regression tests."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / ".github" / "scripts" / "build_dashboard.py"


def build(output: Path) -> None:
    runpy.run_path(str(BUILDER))["build_dashboard"](output, ROOT)


def test_pages_artifact_contains_entrypoint_and_real_report(tmp_path: Path) -> None:
    output = tmp_path / "site"
    build(output)
    assert {p.name for p in output.iterdir()} == {"index.html", "audit-report.json"}
    assert (output / "index.html").read_bytes() == (ROOT / "assets/dashboard.html").read_bytes()
    report = json.loads((output / "audit-report.json").read_text())
    assert report["summary"]["tools_scanned"] > 0
    assert len(report["diagnostics"]) == report["summary"]["total_diagnostics"] > 0
    assert report["dashboard"]["scope"] == "examples/"
    assert datetime.fromisoformat(report["dashboard"]["generated_at"]).tzinfo is not None
    for entry in [*report["tools"], *report["diagnostics"]]:
        assert entry["source_file"].startswith("examples/")
        assert not Path(entry["source_file"]).is_absolute()


def test_pages_archive_has_files_at_root(tmp_path: Path) -> None:
    output = tmp_path / "site"
    build(output)
    archive = tmp_path / "artifact.tar"
    # These are the Linux packaging arguments used by upload-pages-artifact@v3.
    subprocess.run(
        [
            "tar",
            "--dereference",
            "--hard-dereference",
            "--directory",
            str(output),
            "-cf",
            str(archive),
            "--exclude=.git",
            "--exclude=.github",
            ".",
        ],
        check=True,
    )
    with tarfile.open(archive) as artifact:
        assert {m.name for m in artifact if m.isfile()} == {"./index.html", "./audit-report.json"}


def test_dashboard_cli_and_rebuild(tmp_path: Path) -> None:
    output = tmp_path / "site"
    subprocess.run([sys.executable, str(BUILDER), "--output", str(output)], check=True, cwd=ROOT)
    (output / "index.html").write_text("stale")
    build(output)
    assert (output / "index.html").read_bytes() == (ROOT / "assets/dashboard.html").read_bytes()


def test_builder_refuses_unexpected_files(tmp_path: Path) -> None:
    private = tmp_path / "private.txt"
    private.write_text("do not publish")
    with pytest.raises(ValueError, match="unexpected"):
        build(tmp_path)
    assert private.read_text() == "do not publish"


def test_builder_rejects_symlink_output(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        build(link)
    assert list(real.iterdir()) == []


def test_builder_rejects_symlink_file(tmp_path: Path) -> None:
    output = tmp_path / "site"
    output.mkdir()
    private = tmp_path / "private.txt"
    private.write_text("do not overwrite")
    (output / "index.html").symlink_to(private)
    with pytest.raises(ValueError, match="symlink"):
        build(output)
    assert private.read_text() == "do not overwrite"


def test_workflow_separates_pr_builds_from_privileged_deployment() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/dashboard-deploy.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert "pull_request" in workflow["on"]
    assert workflow["permissions"] == {"contents": "read"}
    build_job = workflow["jobs"]["build"]
    upload = next(
        s for s in build_job["steps"] if s.get("uses") == "actions/upload-pages-artifact@v3"
    )
    assert upload["with"]["path"] == "build/pages"
    assert "environment" not in build_job
    deploy = workflow["jobs"]["deploy"]
    assert deploy["needs"] == "build"
    assert deploy["if"] == "github.ref == 'refs/heads/main' && github.event_name != 'pull_request'"
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    assert deploy["environment"]["url"] == "${{ steps.deployment.outputs.page_url }}"
    assert all("checkout" not in s.get("uses", "") and "run" not in s for s in deploy["steps"])
