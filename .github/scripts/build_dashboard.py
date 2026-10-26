"""Prepare a minimal GitHub Pages artifact from bundled, public MCP examples."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mcp_doctor.engine import scan_paths
from mcp_doctor.reporters.json_reporter import render_json

ROOT = Path(__file__).resolve().parents[2]


def build_dashboard(output: Path, root: Path = ROOT) -> None:
    """Write an entry page and a fresh, example-only audit report.

    Args:
        output: Empty destination directory, or a prior two-file dashboard build.
        root: Repository root containing assets/dashboard.html and examples/.

    Raises:
        ValueError: The destination contains unexpected files or symlinks.
        OSError: A source cannot be read or an output cannot be written.
    """
    if output.is_symlink():
        raise ValueError("Dashboard output must not be a symlink")
    output.mkdir(parents=True, exist_ok=True)
    allowed = {"index.html", "audit-report.json"}
    for existing in output.iterdir():
        if existing.is_symlink():
            raise ValueError("Dashboard output contains a symlink")
        if existing.name not in allowed or not existing.is_file():
            raise ValueError(f"Dashboard output contains unexpected content: {existing.name}")

    root = root.resolve()
    # Scan only checked-in examples, not the entire workspace or private inputs.
    # Deliberately vulnerable examples are report data, not a packaging failure.
    report = json.loads(render_json(scan_paths([root / "examples"])))
    for entry in [*report["tools"], *report["diagnostics"]]:
        entry["source_file"] = Path(entry["source_file"]).relative_to(root).as_posix()
    report["dashboard"] = {
        "scope": "examples/",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    shutil.copyfile(root / "assets" / "dashboard.html", output / "index.html")
    (output / "audit-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    """Build the Pages directory selected by the optional --output argument."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "pages")
    args = parser.parse_args()
    build_dashboard(args.output)
    print(f"Prepared index.html and audit-report.json in {args.output}")


if __name__ == "__main__":
    main()
