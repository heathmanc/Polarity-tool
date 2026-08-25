"""Assemble the operator handbook, inlining its figures.

The handbook is published as a single self-contained page, so every screenshot
has to travel inside the HTML as a data URI. Keeping the source as a template
with `{{FIGURE:name|caption}}` tokens means the prose stays reviewable as text
and the figures stay reproducible: regenerate them from the running application
with `capture_manual_screenshots.py`, rebuild, and the handbook shows the
version it documents rather than whatever was on screen the day someone took a
screenshot by hand.

    python scripts/capture_manual_screenshots.py
    python scripts/build_manual.py --output build/manual/operator-manual.html
"""

from __future__ import annotations

import argparse
import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs" / "manual" / "operator-manual.html.in"
FIGURES = ROOT / "docs" / "manual" / "images"

TOKEN = re.compile(r"\{\{FIGURE:([a-z0-9-]+)\|([^}]*)\}\}")


def encode(path: Path) -> tuple[str, int]:
    """Return a data URI for the figure, in whichever format is smaller.

    Screenshots of flat interface panels compress far better as PNG, and the
    ones carrying a photograph of a battery compress far better as JPEG. Picking
    per figure keeps the page small without softening the interface text, which
    a reader has to be able to read.
    """

    import cv2

    image = cv2.imread(str(path))
    if image is None:
        raise SystemExit(f"Could not read figure: {path}")

    ok_png, png = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    ok_jpeg, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not (ok_png and ok_jpeg):
        raise SystemExit(f"Could not encode figure: {path}")

    if len(jpeg) < len(png):
        payload, mime = jpeg, "image/jpeg"
    else:
        payload, mime = png, "image/png"
    encoded = base64.b64encode(payload.tobytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}", len(payload)


def build(output: Path) -> int:
    if not TEMPLATE.is_file():
        raise SystemExit(f"Template not found: {TEMPLATE}")

    template = TEMPLATE.read_text(encoding="utf-8")
    counter = {"n": 0}
    total = {"bytes": 0}
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name, caption = match.group(1), match.group(2).strip()
        source = FIGURES / f"{name}.png"
        if not source.is_file():
            missing.append(name)
            return ""
        uri, size = encode(source)
        counter["n"] += 1
        total["bytes"] += size
        number = counter["n"]
        return (
            f'<figure id="figure-{number}">\n'
            f'  <img src="{uri}" alt="{caption}">\n'
            f"  <figcaption><b>Figure {number}</b><span>{caption}</span></figcaption>\n"
            f"</figure>"
        )

    page = TOKEN.sub(replace, template)
    if missing:
        raise SystemExit(
            "Missing figures: "
            + ", ".join(sorted(missing))
            + "\nRun scripts/capture_manual_screenshots.py first."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")

    print(f"{counter['n']} figures inlined ({total['bytes'] / 1024:,.0f} KB of image data)")
    print(f"{output}  {output.stat().st_size / 1024:,.0f} KB")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the operator handbook.")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build" / "manual" / "operator-manual.html",
        help="Where to write the assembled page.",
    )
    return build(parser.parse_args().output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
