#!/usr/bin/env python3
"""Generate ``assets/demo.svg`` by *running the scanner*.

Why a generator instead of a recorded GIF:

* A recording is a claim about what the tool printed on somebody's laptop, on that day. It
  silently rots, and this project's entire thesis is that stale, unverifiable claims are worse
  than no claim. Running the tool means the demo cannot lie: if the output changes, the asset
  changes.
* It is reviewable. The diff of `demo.svg` is the diff of the tool's output.
* It costs 14 KB and no binary blob.

``--check`` regenerates in memory and compares with the committed file, which is how CI keeps
the demo honest.

The animation is pure CSS (no script, nothing external). If a renderer strips `<style>`, every
line is still visible as a static screenshot, because the visible state is the default and the
animation only hides what has not been "typed" yet.
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets" / "demo.svg"

WIDTH = 1060
LINE_H = 21
PAD_X = 26
PAD_TOP = 74
FONT_SIZE = 13
#: Conservative characters-per-line for the monospace stack at FONT_SIZE, so a long finding
#: wraps inside the frame instead of running off the edge.
MAX_CHARS = 118

# Palette shared with assets/banner.svg, so the README looks like one product.
BG = "#0b1220"
CHROME = "#111c2e"
BORDER = "#1e293b"
TEXT = "#cbd5e1"
DIM = "#64748b"
TEAL = "#2dd4bf"
RED = "#f87171"
AMBER = "#fbbf24"
BLUE = "#60a5fa"
GREEN = "#34d399"
WHITE = "#f8fafc"


@dataclass(frozen=True)
class Line:
    """One rendered terminal line: what it says and how it is coloured."""

    text: str
    color: str = TEXT
    bold: bool = False
    is_prompt: bool = False


def _run(args: list[str], cwd: Path) -> str:
    """Run the CLI and return stdout+stderr, with the checkout path made relative."""
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "mcp_doctor", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    raw = (proc.stdout or "") + (proc.stderr or "")
    return raw.replace(str(cwd) + "/", "").replace(str(cwd), ".").rstrip()


def _colourise(raw: str) -> list[Line]:
    """Map raw scanner output to coloured lines, dropping noise a newcomer does not need."""
    lines: list[Line] = []
    for text in raw.splitlines():
        stripped = text.strip()
        if not stripped:
            lines.append(Line(""))
            continue
        if stripped.startswith("========") or stripped.startswith("--------"):
            lines.append(Line(stripped[:78], DIM))
        elif stripped.startswith("vexar-mcp:"):
            # Machine-readable stderr summary: useful in CI, confusing in a first impression.
            continue
        elif stripped.startswith("[ERROR]"):
            lines.append(Line(stripped, RED))
        elif stripped.startswith("[WARN"):
            lines.append(Line(stripped, AMBER))
        elif stripped.startswith("[INFO"):
            lines.append(Line(stripped, BLUE))
        elif stripped.startswith("Fix:"):
            lines.append(Line(stripped, DIM))
        elif stripped.startswith("Health Score") or "|" in stripped and "Score" in stripped:
            lines.append(Line(stripped, WHITE, bold=True))
        elif stripped.startswith(("FINDINGS", "ERROR -", "CLEAN", "ANALYSIS ISSUES")):
            lines.append(Line(stripped, TEAL, bold=True))
        elif stripped.startswith("Coverage:") or stripped.startswith("Status"):
            lines.append(Line(stripped, TEXT))
        elif stripped.startswith("syntax_error") or stripped.startswith("SyntaxError"):
            lines.append(Line(stripped, RED))
        elif stripped.startswith("No MCP tools") or stripped.startswith("Nothing was graded"):
            lines.append(Line(stripped, AMBER, bold=True))
        else:
            lines.append(Line(stripped))
    return lines


def _wrap(lines: list[Line]) -> list[Line]:
    """Wrap rows that would overflow the frame, keeping a hanging indent."""
    out: list[Line] = []
    for line in lines:
        text = line.text
        if len(text) <= MAX_CHARS:
            out.append(line)
            continue
        indent = "    "
        remaining = text
        first = True
        while remaining:
            width = MAX_CHARS if first else MAX_CHARS - len(indent)
            chunk = remaining[:width]
            if len(remaining) > width:
                cut = chunk.rfind(" ")
                if cut > width // 2:
                    chunk = chunk[:cut]
            out.append(
                Line(chunk if first else indent + chunk, line.color, line.bold, line.is_prompt)
            )
            remaining = remaining[len(chunk) :].lstrip()
            first = False
    return out


def _excerpt_findings(lines: list[Line], keep: int = 3) -> list[Line]:
    """Keep the first ``keep`` findings and say plainly how many were elided."""
    out: list[Line] = []
    shown = 0
    hidden = 0
    skipping = False
    for line in lines:
        is_finding = line.text.startswith("[ERROR]") or line.text.startswith("[WARN")
        is_finding = is_finding or line.text.startswith("[INFO")
        if is_finding:
            shown += 1
            skipping = shown > keep
        if skipping:
            hidden += 1
            continue
        out.append(line)
    if hidden:
        out.append(Line(f"... {hidden} more lines from the same findings (run it to see all)", DIM))
    return out


def scene_findings() -> list[Line]:
    """Scene 1: a scan that finds real problems."""
    raw = _run(["scan", "examples/", "--policy", "strict-prod"], ROOT)
    body = _colourise(raw)
    return [
        Line("$ vexar-mcp scan examples/ --policy strict-prod", GREEN, bold=True, is_prompt=True)
    ] + (_excerpt_findings(body))


def scene_incomplete() -> list[Line]:
    """Scene 2: the differentiator — a scan that could not read its input is never graded."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "broken_server.py"
        target.write_text("def register(:\n", encoding="utf-8")
        raw = _run(["scan", str(target)], ROOT)
        # Normalise the temp path out of the output so the asset is reproducible.
        raw = re.sub(r"/tmp/[^/\s]+/broken_server\.py", "broken_server.py", raw)
        body = _colourise(raw)

    # Kept by *content*, because the score and status share one header row and a prefix match
    # silently dropped the single most important line of the demo.
    wanted = (
        "Health Score",
        "Coverage:",
        "ERROR -",
        "ANALYSIS ISSUES",
        "syntax_error",
        "SyntaxError",
        "Nothing was graded",
    )
    trimmed = [line for line in body if any(key in line.text for key in wanted)]
    return [
        Line(""),
        Line("# a file the scanner could not parse", DIM),
        Line("$ vexar-mcp scan broken_server.py", GREEN, bold=True, is_prompt=True),
        *trimmed,
        Line("$ echo $?", GREEN, bold=True, is_prompt=True),
        Line("3", TEAL, bold=True),
    ]


def _spans(line: Line) -> str:
    """Escape a line for SVG, collapsing leading indentation into a readable prefix."""
    text = line.text
    if text.startswith("  ..."):
        text = text.strip()
    return html.escape(text, quote=False)


def render(scenes: list[list[Line]]) -> str:
    """Render the scenes into one animated SVG."""
    flat: list[tuple[int, Line]] = []
    for index, scene in enumerate(scenes):
        for line in _wrap(scene):
            flat.append((index, line))

    total_lines = len(flat)
    height = PAD_TOP + total_lines * LINE_H + 34
    # Timing: 0.085 s per line, a longer beat between scenes, then a hold before the loop.
    delay = 0.85
    step = 0.085
    pause_between = 1.1
    cycle = delay + total_lines * step + pause_between + 3.4

    body: list[str] = []
    y = PAD_TOP
    previous_scene = 0
    for index, (scene, line) in enumerate(flat):
        if scene != previous_scene:
            delay += pause_between
            previous_scene = scene

        if line.text:
            weight = "600" if line.bold else "400"
            body.append(
                f'      <text class="t{index}" x="{PAD_X}" y="{y}" fill="{line.color}" '
                f'font-weight="{weight}" xml:space="preserve">{_spans(line)}</text>'
            )
        y += LINE_H

    # Per-line keyframes: hidden until its turn, then visible for the rest of the cycle.
    keyframes: list[str] = []
    for index, _ in enumerate(flat):
        on = (delay + index * step) / cycle * 100
        # Hide again briefly at the very end so the loop restarts cleanly.
        off = max(on + 0.4, 99.4)
        keyframes.append(
            f"      @keyframes k{index} {{ 0%, {on:.2f}% {{ opacity: 0 }} "
            f"{min(on + 0.6, off):.2f}%, {off:.2f}% {{ opacity: 1 }} 100% {{ opacity: 1 }} }}"
        )
        keyframes.append(f"      .t{index} {{ animation: k{index} {cycle:.2f}s linear infinite; }}")

    cursor_y = PAD_TOP + total_lines * LINE_H - 4
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="Vexar-MCP scanning an insecure example server, then refusing to grade a file it could not parse">
  <title>Vexar-MCP: findings on an insecure server, then "nothing to grade" on an unparseable one</title>
  <desc>Terminal recording generated from real output: a scan of examples/ reports findings and a health score of 8/100; a scan of an unparseable file reports "Health Score: n/a", Status ERROR, and exit code 3.</desc>
  <style>
    text {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace; font-size: {FONT_SIZE}px; }}
{chr(10).join(keyframes)}
    .cursor {{ animation: blink 1.05s steps(1) infinite; }}
    @keyframes blink {{ 0%, 49% {{ opacity: 1 }} 50%, 100% {{ opacity: 0 }} }}
    @media (prefers-reduced-motion: reduce) {{
      text {{ animation: none !important; opacity: 1 !important; }}
      .cursor {{ animation: none !important; }}
    }}
  </style>
  <rect x="0" y="0" width="{WIDTH}" height="{height}" rx="14" fill="{BG}"/>
  <rect x="0" y="0" width="{WIDTH}" height="{height}" rx="14" fill="none" stroke="{BORDER}"/>
  <path d="M0 44 H{WIDTH}" stroke="{BORDER}"/>
  <circle cx="24" cy="22" r="6" fill="#f87171"/><circle cx="46" cy="22" r="6" fill="#fbbf24"/><circle cx="68" cy="22" r="6" fill="#34d399"/>
  <text x="{WIDTH // 2}" y="27" fill="{DIM}" text-anchor="middle">vexar-mcp — generated by scripts/make_demo_svg.py from real output</text>
{chr(10).join(body)}
  <rect class="cursor" x="{PAD_X}" y="{cursor_y}" width="8" height="15" fill="{TEAL}"/>
</svg>
"""


def build() -> str:
    """Produce the SVG text."""
    return render([scene_findings(), scene_incomplete()])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate assets/demo.svg from real output.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the committed asset differs from a fresh run.",
    )
    args = parser.parse_args(argv)

    svg = build()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != svg:
            print(
                "assets/demo.svg is stale: the tool's output changed. "
                "Run `python scripts/make_demo_svg.py` and review the diff.",
                file=sys.stderr,
            )
            return 1
        print("assets/demo.svg is up to date with the tool's real output")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(svg, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(svg) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
