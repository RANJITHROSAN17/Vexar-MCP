"""Tests for the release verifier that gates publishing.

Issue #50 is a release-automation issue, so its tests are about *refusing* to publish: the
expensive failure is not a red CI run, it is a version that reaches PyPI under a tag nobody
expected, or a workflow that silently publishes nothing for months. Each test below pins one
of those failures to a message a maintainer can act on.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "verify_release.py"

spec = importlib.util.spec_from_file_location("verify_release", SCRIPT)
assert spec is not None and spec.loader is not None
verify = importlib.util.module_from_spec(spec)
sys.modules["verify_release"] = verify
spec.loader.exec_module(verify)

VERSION = "1.2.3"

GOOD_PUBLISH_WORKFLOW = """
name: Publish
on:
  release:
    types: [published]
  workflow_call:
    inputs:
      tag:
        type: string
jobs:
  publish:
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/vexar-mcp
    permissions:
      id-token: write
    steps:
      - uses: pypa/gh-action-pypi-publish@release/v1
"""

GOOD_RELEASE_WORKFLOW = """
name: Release
on:
  push:
    tags: ["v*.*.*"]
jobs:
  publish:
    uses: ./.github/workflows/publish-pypi.yml
"""


def make_root(tmp_path: Path, *, version: str = VERSION, name: str = "Vexar-MCP") -> Path:
    """Build the smallest tree the verifier reads: metadata, changelog, two workflows."""
    (tmp_path / "src" / "mcp_doctor").mkdir(parents=True)
    (tmp_path / "src" / "mcp_doctor" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [Unreleased]\n\n## [{version}] - 2026-10-30\n\n### Added\n\n- thing\n",
        encoding="utf-8",
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "publish-pypi.yml").write_text(GOOD_PUBLISH_WORKFLOW, encoding="utf-8")
    (workflows / "release.yml").write_text(GOOD_RELEASE_WORKFLOW, encoding="utf-8")
    return tmp_path


class TestRepositoryIsReleasable:
    def test_the_repository_passes_its_own_verifier(self) -> None:
        assert verify.main(["--quiet"]) == 0

    def test_the_repository_passes_with_its_current_version_as_a_tag(self) -> None:
        version = verify.package_version(ROOT)
        assert verify.main(["--quiet", "--tag", f"v{version}"]) == 0


class TestVersionAgreement:
    def test_a_stale_dunder_version_is_reported(self, tmp_path: Path) -> None:
        root = make_root(tmp_path, version="1.2.3")
        (root / "src" / "mcp_doctor" / "__init__.py").write_text('__version__ = "1.2.2"\n')
        problems = verify.check_versions(root, tag=None)
        assert any("1.2.3" in p and "1.2.2" in p and "stale" in p for p in problems)

    def test_a_tag_that_disagrees_with_the_package_is_reported(self, tmp_path: Path) -> None:
        problems = verify.check_versions(make_root(tmp_path), tag="v1.2.4")
        assert any("v1.2.4" in p and "1.2.3" in p for p in problems)

    def test_the_matching_tag_is_accepted(self, tmp_path: Path) -> None:
        assert verify.check_versions(make_root(tmp_path), tag="v1.2.3") == []

    @pytest.mark.parametrize("version", ["1.2", "1.2.3.dev0", "1.2.3rc1"])
    def test_a_non_release_version_is_reported(self, tmp_path: Path, version: str) -> None:
        root = make_root(tmp_path, version=version)
        assert any("MAJOR.MINOR.PATCH" in p for p in verify.check_versions(root, tag=None))

    def test_the_version_is_found_without_importing_the_package(self, tmp_path: Path) -> None:
        """Importing would drag in the engine, the loaders and any plugin on the path."""
        root = make_root(tmp_path, version="9.9.9")
        assert verify.package_version(root) == "9.9.9"


class TestChangelog:
    def test_a_missing_section_for_the_packaged_version_is_reported(self, tmp_path: Path) -> None:
        root = make_root(tmp_path)
        (root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
        problems = verify.check_changelog(root, VERSION)
        assert any("Unreleased" in p for p in problems)

    def test_an_undated_heading_does_not_count(self, tmp_path: Path) -> None:
        """`## [1.2.3]` with no date is a section nobody can date on PyPI."""
        root = make_root(tmp_path)
        (root / "CHANGELOG.md").write_text(f"## [{VERSION}]\n", encoding="utf-8")
        assert verify.check_changelog(root, VERSION)


class TestDistributionName:
    @pytest.mark.parametrize("name", ["Vexar-MCP", "vexar_mcp", "VEXAR.MCP"])
    def test_normalized_equivalents_are_accepted(self, tmp_path: Path, name: str) -> None:
        assert verify.check_distribution_name(make_root(tmp_path, name=name)) == []

    def test_a_renamed_distribution_is_reported(self, tmp_path: Path) -> None:
        """The trusted publisher is bound to a name; a rename here publishes to nowhere."""
        problems = verify.check_distribution_name(make_root(tmp_path, name="mcp-doctor"))
        assert any("vexar-mcp" in p and "mcp-doctor" in p for p in problems)


class TestPublishWorkflowWiring:
    def test_twine_upload_is_reported(self, tmp_path: Path) -> None:
        """The bug this repository actually shipped: OIDC requested, token never usable."""
        root = make_root(tmp_path)
        path = root / ".github" / "workflows" / "publish-pypi.yml"
        path.write_text(
            GOOD_PUBLISH_WORKFLOW.replace(
                "      - uses: pypa/gh-action-pypi-publish@release/v1",
                "      - run: python -m twine upload dist/*",
            ),
            encoding="utf-8",
        )
        problems = verify.check_publish_workflow(root)
        assert any("twine upload" in p for p in problems)
        assert any("pypa/gh-action-pypi-publish" in p for p in problems)

    def test_mentioning_twine_upload_in_a_comment_is_not_reported(self, tmp_path: Path) -> None:
        """The workflow explains the old bug in a comment; prose is not a command."""
        root = make_root(tmp_path)
        path = root / ".github" / "workflows" / "publish-pypi.yml"
        path.write_text(
            GOOD_PUBLISH_WORKFLOW.replace(
                "    steps:",
                "    steps:\n      # `twine upload` cannot exchange an OIDC token.",
            ),
            encoding="utf-8",
        )
        assert verify.check_publish_workflow(root) == []

    def test_a_missing_oidc_permission_is_reported(self, tmp_path: Path) -> None:
        root = make_root(tmp_path)
        path = root / ".github" / "workflows" / "publish-pypi.yml"
        path.write_text(
            GOOD_PUBLISH_WORKFLOW.replace("      id-token: write", "      contents: read"),
            encoding="utf-8",
        )
        assert any("id-token: write" in p for p in verify.check_publish_workflow(root))

    def test_a_missing_environment_is_reported(self, tmp_path: Path) -> None:
        root = make_root(tmp_path)
        path = root / ".github" / "workflows" / "publish-pypi.yml"
        path.write_text(
            GOOD_PUBLISH_WORKFLOW.replace("      name: pypi\n", "      name: release\n"),
            encoding="utf-8",
        )
        assert any("environment" in p for p in verify.check_publish_workflow(root))

    def test_a_workflow_that_cannot_be_called_is_reported(self, tmp_path: Path) -> None:
        root = make_root(tmp_path)
        path = root / ".github" / "workflows" / "publish-pypi.yml"
        path.write_text(
            GOOD_PUBLISH_WORKFLOW.replace("  workflow_call:\n", "  ignored_trigger:\n"),
            encoding="utf-8",
        )
        assert any("not callable" in p for p in verify.check_publish_workflow(root))


class TestReleaseChain:
    def test_a_release_that_does_not_call_the_publisher_is_reported(self, tmp_path: Path) -> None:
        """GITHUB_TOKEN-authored releases do not trigger other workflows, so the call is the
        only thing that makes a tag push publish anything."""
        root = make_root(tmp_path)
        (root / ".github" / "workflows" / "release.yml").write_text(
            "name: Release\non:\n  push:\n    tags: ['v*']\njobs:\n  r:\n    runs-on: x\n",
            encoding="utf-8",
        )
        problems = verify.check_release_chain(root)
        assert any("GITHUB_TOKEN" in p for p in problems)

    def test_the_repository_chains_tag_to_publish(self) -> None:
        assert verify.check_release_chain(ROOT) == []


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class TestPyPIState:
    def test_a_project_that_does_not_exist_yet_points_at_the_pending_publisher(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A first publish cannot add a publisher to a project page that does not exist."""

        def raise_404(*args: object, **kwargs: object) -> _FakeResponse:
            raise urllib.error.HTTPError("url", 404, "not found", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(verify.urllib.request, "urlopen", raise_404)
        problems = verify.check_pypi(make_root(tmp_path))
        assert any("pending" in p.lower() and "publishing" in p for p in problems)

    def test_an_already_published_version_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PyPI refuses a duplicate upload, so this must fail before the run, not during it."""
        payload = {"releases": {VERSION: [{"filename": "vexar_mcp-1.2.3-py3-none-any.whl"}]}}
        monkeypatch.setattr(
            verify.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload)
        )
        problems = verify.check_pypi(make_root(tmp_path))
        assert any("already serves" in p and VERSION in p for p in problems)

    def test_an_unpublished_version_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            verify.urllib.request, "urlopen", lambda *a, **k: _FakeResponse({"releases": {}})
        )
        assert verify.check_pypi(make_root(tmp_path)) == []


class TestCommandLine:
    def test_problems_are_reported_on_stderr_with_a_nonzero_exit(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = make_root(tmp_path)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "Vexar-MCP"\nversion = "2.0.0"\n', encoding="utf-8"
        )
        assert verify.main(["--root", str(root)]) == 1
        captured = capsys.readouterr()
        assert "problem(s) found" in captured.err
        assert "stale" in captured.err
