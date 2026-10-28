#!/usr/bin/env python3
"""Compose ``assets/social-preview.png`` (1280x640) from the real demo output.

This is the image GitHub shows in link unfurls, the social preview, and every chat app someone
pastes the repo into — usually the first pixel a stranger ever sees. It is generated from the
same terminal capture the README uses, so it cannot advertise behaviour the tool does not have.

DejaVu is used because it ships with virtually every Linux image; no font download, no network.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "social-preview.png"
SVG = ROOT / "assets" / "demo.svg"

W, H = 1280, 640
BG = (11, 18, 32)
PANEL = (17, 28, 46)
BORDER = (30, 41, 59)
TEAL = (45, 212, 191)
WHITE = (248, 250, 252)
TEXT = (203, 213, 225)
DIM = (100, 116, 139)
RED = (248, 113, 113)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
SANS_BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"
SANS = FONT_DIR / "DejaVuSans.ttf"
MONO = FONT_DIR / "DejaVuSansMono.ttf"
MONO_BOLD = FONT_DIR / "DejaVuSansMono-Bold.ttf"


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    """Load a TrueType font, failing loudly rather than shipping a mojibake card."""
    if not path.is_file():
        raise SystemExit(f"missing font: {path}")
    return ImageFont.truetype(str(path), size)


def render_terminal(width: int) -> Image.Image:
    """Rasterise the committed demo SVG to a PNG at the requested width."""
    if not SVG.is_file():
        raise SystemExit("run scripts/make_demo_svg.py first")
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "demo.png"
        subprocess.run(  # noqa: S603 - fixed argv
            [
                "convert",
                "-background",
                "none",
                "-density",
                "160",
                str(SVG),
                "-resize",
                f"{width}x",
                str(png),
            ],
            check=True,
            capture_output=True,
        )
        return Image.open(png).convert("RGBA")


def pill(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, accent: tuple[int, int, int]
) -> int:
    """Draw a rounded chip and return the x position just past it."""
    f = font(SANS_BOLD, 15)
    left, top = xy
    tw = int(draw.textlength(text, font=f))
    pad = 14
    box = (left, top, left + tw + pad * 2, top + 32)
    draw.rounded_rectangle(box, radius=16, fill=(24, 38, 60), outline=accent, width=1)
    draw.text((left + pad, top + 8), text, font=f, fill=accent)
    return box[2] + 10


def build() -> None:
    """Draw the card and save it."""
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Subtle top accent rule, so the card reads as designed rather than cropped.
    draw.rectangle((0, 0, W, 5), fill=TEAL)

    # ---- left: identity -------------------------------------------------
    x = 56
    draw.text((x, 74), "Vexar-MCP", font=font(SANS_BOLD, 62), fill=WHITE)

    tagline = font(SANS, 23)
    draw.text((x, 156), "Security, schema and token-cost checks", font=tagline, fill=TEXT)
    draw.text((x, 188), "for Model Context Protocol servers.", font=tagline, fill=TEXT)

    draw.text(
        (x, 240),
        "Finds the well-known MCP failure modes before you ship:",
        font=font(SANS, 18),
        fill=DIM,
    )
    mono = font(MONO, 17)
    for i, line in enumerate(
        (
            "> shell injection, SSRF, path traversal, secrets",
            "> schemas an LLM cannot route or afford",
            "> and says 'n/a' when it could not read your code",
        )
    ):
        draw.text((x, 272 + i * 27), line, font=mono, fill=TEAL if i < 2 else RED)

    # ---- chips, two rows so nothing spills under the terminal -------------
    rows = (
        (("14 rules", TEAL), ("0 runtime deps", TEAL)),
        (("SARIF + HTML", TEAL), ("exit 3 = blind spot", RED)),
    )
    for row_index, row in enumerate(rows):
        cx = x
        for label, accent in row:
            cx = pill(draw, (cx, 366 + row_index * 42), label, accent)

    draw.text(
        (x, 470),
        "$ vexar-mcp scan . --policy strict-prod",
        font=font(MONO_BOLD, 18),
        fill=WHITE,
    )
    draw.text(
        (x, 508),
        "github.com/RANJITHROSAN17/Vexar-MCP",
        font=font(SANS_BOLD, 20),
        fill=TEAL,
    )
    draw.text(
        (x, 542),
        "Python 3.10+  ·  MIT  ·  your code is never executed",
        font=font(SANS, 16),
        fill=DIM,
    )

    # ---- right: the real terminal --------------------------------------
    term = render_terminal(600)
    max_h = H - 88
    if term.height > max_h:
        scale = max_h / term.height
        term = term.resize((int(term.width * scale), max_h), Image.LANCZOS)
    pad = 12
    panel_w, panel_h = term.width + pad * 2, term.height + pad * 2
    px, py = W - panel_w - 40, (H - panel_h) // 2
    draw.rounded_rectangle(
        (px - 6, py - 6, px + panel_w + 6, py + panel_h + 6),
        radius=18,
        fill=PANEL,
        outline=BORDER,
        width=1,
    )
    img.paste(term, (px + pad, py + pad), term)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB, {W}x{H})")


if __name__ == "__main__":
    sys.exit(build())
