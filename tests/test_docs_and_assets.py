"""Guards for the first thing anyone sees: the README, the demo, and the quickstart.

These tests exist because onboarding assets are the least-tested, fastest-rotting part of any
repository. A demo that shows output the tool no longer produces, or a quickstart that tells a
newcomer to run a command that no longer exists, is worse than no asset at all — it spends the
one minute of attention a stranger is willing to give.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_doctor import __version__
from mcp_doctor.config import load_config
from mcp_doctor.engine import scan_paths
from mcp_doctor.models import ScanConfig

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
assert spec is not None and spec.loader is not None
docs = importlib.util.module_from_spec(spec)
sys.modules["check_docs"] = docs
spec.loader.exec_module(docs)


class TestDocumentationLinks:
    def test_every_relative_link_resolves(self) -> None:
        problems = [p for md in docs.markdown_files() for p in docs.check_file(md, ROOT)]
        assert not problems, "\n".join(problems)

    def test_checker_catches_a_missing_file(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("[x](missing.md)\n", encoding="utf-8")
        assert docs.check_file(tmp_path / "a.md", tmp_path)

    def test_checker_catches_a_dead_anchor(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("# Real heading\n\n[x](#nope)\n", encoding="utf-8")
        assert "anchor" in docs.check_file(tmp_path / "a.md", tmp_path)[0]

    def test_checker_accepts_html_anchors_and_github_slugs(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text(
            '<a id="mcp001"></a>\n\n## Step 0 — what you need\n\n'
            "[a](#mcp001) [b](#step-0--what-you-need)\n",
            encoding="utf-8",
        )
        assert docs.check_file(tmp_path / "a.md", tmp_path) == []


class TestDemoAsset:
    def test_committed_demo_matches_the_tool_output(self) -> None:
        """The demo is generated from real output; if that changed, the asset must change."""
        result = subprocess.run(  # noqa: S603 - fixed argv
            [sys.executable, str(ROOT / "scripts" / "make_demo_svg.py"), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_demo_is_animated_but_survives_without_css(self) -> None:
        svg = (ROOT / "assets" / "demo.svg").read_text(encoding="utf-8")
        assert "@keyframes" in svg, "the demo should animate"
        # Static fallback: the default rendered state must be visible, not hidden behind JS.
        assert "<script" not in svg.lower(), (
            "no script: the asset must render in a sandboxed iframe"
        )
        assert "prefers-reduced-motion" in svg, "respect reduced-motion"
        assert "http" not in svg.replace("http://www.w3.org/2000/svg", ""), "no external assets"

    def test_demo_shows_both_the_findings_and_the_refusal(self) -> None:
        svg = (ROOT / "assets" / "demo.svg").read_text(encoding="utf-8")
        # Derived from the *same* command the demo runs, not from one file: the demo scans
        # `examples/` under strict-prod, and deriving from a narrower target made this test
        # pass by coincidence rather than by agreement with the asset.
        report = scan_paths([ROOT / "examples"], load_config(policy_override="strict-prod"))
        assert report.summary.score is not None
        assert f"Health Score: {report.summary.score}/100" in svg, (
            "should show a real findings scan"
        )
        assert "n/a (nothing to grade)" in svg, "should show the incomplete-analysis refusal"
        assert "exit 3" in svg or ">3<" in svg

    def test_demo_stays_small_enough_to_render_inline(self) -> None:
        size = (ROOT / "assets" / "demo.svg").stat().st_size
        assert size < 60_000, f"demo.svg is {size} bytes; GitHub renders small SVGs inline"


class TestSocialPreview:
    def test_card_exists_at_github_recommended_size(self) -> None:
        """Read the PNG header directly: this project has no runtime or dev image dependency."""
        import struct

        path = ROOT / "assets" / "social-preview.png"
        assert path.is_file(), "run scripts/make_social_preview.py"
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
        assert data[12:16] == b"IHDR"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (1280, 640), f"is {width}x{height}, GitHub wants 1280x640"
        assert path.stat().st_size < 1_000_000, "GitHub caps the social preview at 1 MB"


class TestQuickstart:
    GUIDE = ROOT / "QUICKSTART.md"

    def test_exists_and_is_linked_from_the_readme(self) -> None:
        assert self.GUIDE.is_file()
        assert "QUICKSTART.md" in (ROOT / "README.md").read_text(encoding="utf-8")

    def test_every_command_it_tells_a_newcomer_to_run_is_real(self) -> None:
        """Any `vexar-mcp <subcommand>` in the guide must be a registered subcommand."""
        from mcp_doctor.cli import _build_parser

        known = set()
        for action in _build_parser()._subparsers._group_actions:  # type: ignore[union-attr]
            known |= set(action.choices)  # type: ignore[attr-defined]
        used = set(re.findall(r"vexar-mcp ([a-z-]+)", self.GUIDE.read_text(encoding="utf-8")))
        unknown = {name for name in used if name not in known and not name.startswith("-")}
        assert not unknown, f"quickstart documents unknown subcommand(s): {sorted(unknown)}"

    def test_quotes_the_current_version(self) -> None:
        text = self.GUIDE.read_text(encoding="utf-8")
        versions = set(re.findall(r"vexar-mcp (\d+\.\d+\.\d+)", text))
        assert versions == {__version__}, f"guide shows {versions}, package is {__version__}"

    def test_covers_the_four_onboarding_steps(self) -> None:
        text = self.GUIDE.read_text(encoding="utf-8")
        for heading in ("## Step 0", "## Step 1", "## Step 2", "## Step 3", "## Step 4"):
            assert heading in text

    def test_explains_what_each_exit_code_means(self) -> None:
        text = self.GUIDE.read_text(encoding="utf-8")
        for code, meaning in (
            ("exit `3`", "incomplete"),
            ("exit `4`", "no matches"),
            ("exit `2`", "configuration"),
        ):
            assert code in text, f"guide does not mention {code}"
            assert meaning in text.lower(), f"guide does not explain {code}"


class TestReadmeAboveTheFold:
    def test_leads_with_the_demo_before_the_first_detail_section(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "assets/demo.svg" in readme
        assert readme.index("assets/demo.svg") < readme.index("## What it checks")

    def test_answers_what_who_and_why_before_configuration(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for phrase in ("## What this is", "## Quick start"):
            assert phrase in readme

    def test_quick_start_is_three_steps_or_fewer(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        block = readme[readme.index("## Quick start") : readme.index("## See it work")]
        numbered = re.findall(r"^# \d\.", block, flags=re.MULTILINE)
        assert 1 <= len(numbered) <= 3, f"quick start has {len(numbered)} numbered steps"


@pytest.mark.parametrize(
    "asset", ["assets/banner.svg", "assets/demo.svg", "assets/social-preview.png"]
)
def test_referenced_assets_exist(asset: str) -> None:
    assert (ROOT / asset).is_file()


class TestQuotedOutputIsReal:
    """Scores quoted in prose must be scores the tool actually produces.

    The onboarding docs show real terminal output, which is the point -- but it means every
    rule change moves those numbers. A literal that nobody re-derives silently becomes a
    claim about behaviour that no longer exists, and it is the first thing a newcomer
    compares against when they run the command themselves.
    """

    DOCS = ("README.md", "QUICKSTART.md", "docs/usage.md", "marketing/DEVTO_DRAFT.md")

    def _real_scores(self) -> set[int]:
        scores = set()
        for name in ("vulnerable_server.py", "clean_server.py"):
            report = scan_paths([ROOT / "examples" / name], ScanConfig())
            if report.summary.score is not None:
                scores.add(report.summary.score)
        return scores

    def test_every_quoted_health_score_is_one_the_tool_produces(self) -> None:
        real = self._real_scores()
        offenders: list[str] = []
        for name in self.DOCS:
            text = (ROOT / name).read_text(encoding="utf-8")
            for match in re.finditer(r"Health Score: (\d+)/100", text):
                if int(match.group(1)) not in real:
                    offenders.append(f"{name}: {match.group(0)}")
            for match in re.finditer(r"\*\*(\d+)/100\*\*", text):
                if int(match.group(1)) not in real:
                    offenders.append(f"{name}: **{match.group(1)}/100**")
        assert not offenders, (
            "documented scores the tool does not produce: "
            f"{offenders} (it currently produces {sorted(real)})"
        )

    def test_every_subcommand_is_documented_in_the_usage_reference(self) -> None:
        """A command nobody documented is a command nobody finds."""
        from mcp_doctor.cli import _build_parser

        known = set()
        for action in _build_parser()._subparsers._group_actions:  # type: ignore[union-attr]
            known |= set(action.choices)  # type: ignore[attr-defined]
        text = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"### `vexar-mcp ([a-z-]+)", text))
        assert not (known - documented), f"undocumented subcommand(s): {sorted(known - documented)}"


def test_the_composite_action_installs_yaml_support() -> None:
    """The action is the main CI entry point; it must read every format the CLI documents.

    Without the extra, a repository with YAML manifests reports an unanalyzed input under
    the action while the CLI reads it fine -- a divergence users would only discover from a
    mysterious PARTIAL status.
    """
    action = (ROOT / "action.yml").read_text(encoding="utf-8")
    install_lines = [line for line in action.splitlines() if "pip install" in line]
    assert install_lines, "the action should install the package"
    assert any("[yaml]" in line for line in install_lines), (
        f"the composite action does not install the yaml extra: {install_lines}"
    )


def test_every_workflow_that_scans_this_repository_can_read_every_format() -> None:
    """A job that scans `examples/` must install the extras the scan needs.

    `examples/` contains a YAML manifest, so an install without `[yaml]` reports an
    unanalyzed input, exits 3, and fails the job under `set -e`. That failure is confusing
    because nothing is wrong with the code being scanned -- and the composite action already
    installs the extra, so a workflow calling it is fine.
    """
    offenders = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        if "vexar-mcp scan examples/" not in text and "vexar-mcp scan .github/" not in text:
            continue
        if "uses: ./" in text:  # delegates to the composite action, which installs [yaml]
            continue
        installs = [line for line in text.splitlines() if "pip install" in line]
        if not any("[yaml]" in line or "[dev]" in line for line in installs):
            offenders.append(path.name)
    assert not offenders, f"workflow(s) scan YAML manifests but install no yaml extra: {offenders}"


class TestActionPinConsistency:
    """One major per action, across every workflow.

    Drift is not caught by any workflow run: two jobs using `checkout@v4` and `checkout@v7`
    both pass, and the difference only shows up when one of them starts behaving
    differently. That is how `setup-python` ended up split across v5, v6 and v7 -- a
    Dependabot PR was closed unmerged and four files were bumped by hand instead, so the
    five it did not touch stayed behind.

    This is a ratchet. The allowlist names the actions still carrying drift, so the test is
    green today; fixing one makes it fail until the entry is deleted, which keeps the list
    from going stale.
    """

    # Tracked in RM-035. Delete entries as they are unified -- do not add new ones.
    KNOWN_DRIFT = frozenset({"actions/checkout", "actions/github-script"})

    def _pins(self) -> dict[str, set[str]]:
        pins: dict[str, set[str]] = {}
        sources = [*(ROOT / ".github" / "workflows").glob("*.y*ml"), ROOT / "action.yml"]
        for path in sources:
            for match in re.finditer(
                r"uses:\s*([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)@(\S+)", path.read_text(encoding="utf-8")
            ):
                pins.setdefault(match.group(1), set()).add(match.group(2))
        return {action: refs for action, refs in pins.items() if len(refs) > 1}

    def test_no_action_is_pinned_to_two_majors_outside_the_known_list(self) -> None:
        drift = self._pins()
        unexpected = {
            action: sorted(refs) for action, refs in drift.items() if action not in self.KNOWN_DRIFT
        }
        assert not unexpected, f"action(s) pinned to more than one ref: {unexpected}"

    def test_the_allowlist_has_no_stale_entries(self) -> None:
        fixed = self.KNOWN_DRIFT - set(self._pins())
        assert not fixed, (
            f"these actions no longer drift, so remove them from KNOWN_DRIFT: {sorted(fixed)}"
        )

    def test_setup_python_is_uniform(self) -> None:
        """The specific drift this PR fixed; a one-line guard against regression."""
        pins = {
            ref
            for path in (ROOT / ".github" / "workflows").glob("*.y*ml")
            for ref in re.findall(r"actions/setup-python@(\S+)", path.read_text(encoding="utf-8"))
        }
        pins |= set(
            re.findall(
                r"actions/setup-python@(\S+)", (ROOT / "action.yml").read_text(encoding="utf-8")
            )
        )
        assert pins == {"v7"}, f"setup-python pins should all be v7, found {sorted(pins)}"
