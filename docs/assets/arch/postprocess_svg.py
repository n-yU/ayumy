"""Prepare an exported draw.io SVG for display as a README image.

README images are rendered through <img>, which cannot fetch web fonts,
so the glyphs the diagram uses are inlined as WOFF2 data URIs.
"""

import base64
import io
import re
import string
import sys
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

FONT_FAMILY = "M PLUS 1p"
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/mplus1p/MPLUS1p-{weight}.ttf"
WEIGHTS = {"Regular": 400, "Bold": 700}
STYLE_ID = "embedded-fonts"


def subset_woff2(weight_name: str, text: str) -> str:
    """Return the given weight of M PLUS 1p, cut down to `text`, as base64 WOFF2."""
    with urllib.request.urlopen(FONT_URL.format(weight=weight_name)) as response:
        font = TTFont(io.BytesIO(response.read()))
    subsetter = subset.Subsetter(subset.Options())
    subsetter.populate(text=text)
    subsetter.subset(font)
    font.flavor = "woff2"
    buffer = io.BytesIO()
    font.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def postprocess(svg_path: Path) -> None:
    """Drop the PNG fallbacks of HTML labels and embed a fresh font subset."""
    svg = svg_path.read_text(encoding="utf-8")
    # Browsers render foreignObject even in <img>, so the per-label PNG fallback only inflates the file
    svg = re.sub(r"(</foreignObject>)<image\b[^>]*/>", r"\1", svg)
    svg = re.sub(rf'<style id="{STYLE_ID}">.*?</style>', "", svg, flags=re.DOTALL)
    # Non-ASCII glyphs are taken from the SVG itself so labels can change without editing this script
    text = string.printable + "".join(sorted({ch for ch in svg if ord(ch) > 0x7F}))
    faces = "".join(
        f"@font-face{{font-family:'{FONT_FAMILY}';font-weight:{weight};"
        f"src:url(data:font/woff2;base64,{subset_woff2(name, text)}) format('woff2');}}"
        for name, weight in WEIGHTS.items()
    )
    style = f'<style id="{STYLE_ID}">{faces}</style>'
    svg, count = re.subn(
        r"<svg\b[^>]*>", lambda match: match.group(0) + style, svg, count=1
    )
    if count != 1:
        sys.exit(f"no <svg> element found in {svg_path}")
    svg_path.write_text(svg, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: postprocess_svg.py <svg>")
    postprocess(Path(sys.argv[1]))
