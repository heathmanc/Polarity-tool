"""Extract the PLC section of the handbook as a standalone printable document.

A controls engineer commissioning the interface needs the tag map, the
sequencing, and the watchdog logic at the panel -- not the whole operator
handbook. Cutting the section out by hand would fork it: the handout and the
handbook would drift, and the one at the panel would be the stale one. This
lifts the section straight out of the handbook source, so there is one text.

    python scripts/build_plc_handout.py --output build/manual/plc-commissioning.pdf

Rendering needs Chromium. The bundled Playwright browser is used when present;
otherwise pass --chromium with a path, or --html-only to stop at the HTML.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs" / "manual" / "operator-manual.html.in"

SECTION = re.compile(
    r'<section class="part" id="plc">(.*?)</section>', re.S
)
STYLE = re.compile(r"<style>(.*?)</style>", re.S)
FIGURE_TOKEN = re.compile(r"\{\{FIGURE:([a-z0-9-]+)\|([^}]*)\}\}")

CHROMIUM_CANDIDATES = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell",
)

# Printed on paper under panel lighting, so the page commits to the light
# palette rather than following a viewer theme, and the rail, hover states, and
# shadows that only make sense on screen are dropped.
PRINT_CSS = """
:root { color-scheme: light; }
body {
  background: #FFFFFF;
  font-size: 10.5pt;
  line-height: 1.5;
}
.sheet { max-width: 100%; margin: 0; padding: 0; }
.cover { border-bottom: 3px solid var(--ink); padding-bottom: 18px; margin-bottom: 4px; }
.cover h1 { font-size: 30pt; margin-bottom: 10px; }
.cover .standfirst { font-size: 12pt; margin-bottom: 20px; }
.part { padding-top: 22px; }
h2 { font-size: 19pt; }
h3 { font-size: 13.5pt; margin-top: 22px; }
h4 { font-size: 11pt; margin-top: 16px; }
p, ul, ol, .note, table { max-width: none; }
figure img { box-shadow: none; }
pre { font-size: 9pt; }
table { font-size: 9.5pt; min-width: 0; }
th, td { padding: 5px 9px; }
.diagram { padding: 14px 10px 8px; }
.diagram svg { min-width: 0; }

/* Keep a rule, a table, or a diagram from being cut in half by a page break. */
h2, h3, h4 { break-after: avoid; page-break-after: avoid; }
.tablewrap, .note, .diagram, figure, pre { break-inside: avoid; page-break-inside: avoid; }
tr { break-inside: avoid; page-break-inside: avoid; }

@page {
  size: Letter;
  margin: 16mm 15mm 18mm;
}
"""


def find_chromium(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for candidate in CHROMIUM_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("chromium") or shutil.which("google-chrome")


def inline_figures(html: str) -> str:
    import base64

    import cv2

    def replace(match: re.Match[str]) -> str:
        name, caption = match.group(1), match.group(2).strip()
        source = ROOT / "docs" / "manual" / "images" / f"{name}.png"
        if not source.is_file():
            return ""
        image = cv2.imread(str(source))
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return ""
        uri = "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode()
        return (
            f'<figure>\n  <img src="{uri}" alt="{caption}">\n'
            f"  <figcaption><b>Figure 1</b><span>{caption}</span></figcaption>\n</figure>"
        )

    return FIGURE_TOKEN.sub(replace, html)


def assemble() -> str:
    template = TEMPLATE.read_text(encoding="utf-8")

    style_match = STYLE.search(template)
    section_match = SECTION.search(template)
    if style_match is None or section_match is None:
        raise SystemExit("Could not find the stylesheet or the PLC section in the handbook.")

    section = inline_figures(section_match.group(1))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Pole Position PLC Commissioning</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{style_match.group(1)}</style>
<style>{PRINT_CSS}</style>
</head>
<body>
<div class="sheet">
<header class="cover">
  <p class="eyebrow">Pole Position &middot; Battery polarity inspection</p>
  <h1>PLC Commissioning</h1>
  <p class="standfirst">Tag map, cycle sequencing, heartbeat and watchdog, recipe selection, bypass logic, and the commissioning steps.</p>
  <dl class="docmeta">
    <div><dt>Applies to</dt><dd>v0.32.0</dd></div>
    <div><dt>Document</dt><dd>Rev D</dd></div>
    <div><dt>Status</dt><dd>Unapproved draft</dd></div>
    <div><dt>Extract of</dt><dd>Station handbook, part 03</dd></div>
  </dl>
</header>
<section class="part" id="plc">{section}</section>
<footer>
  <p><strong>Extracted from the Pole Position station handbook, part 03, for application v0.32.0.</strong>
  Rebuild with <code>scripts/build_plc_handout.py</code> so this handout and the handbook cannot drift apart.</p>
  <p>Pole Position is a quality inspection system. It is not a safety PLC, a guard, or an
  emergency stop. The controller owns the line permissive, reject, stop, and bypass logic.
  The bypass tag is an operational quality bypass and is not a safety-rated function.</p>
</footer>
</div>
</body>
</html>
"""


def build(output: Path, chromium: str | None, html_only: bool) -> int:
    html = assemble()
    output.parent.mkdir(parents=True, exist_ok=True)

    if html_only:
        target = output.with_suffix(".html")
        target.write_text(html, encoding="utf-8")
        print(f"{target}  {target.stat().st_size / 1024:,.0f} KB")
        return 0

    binary = find_chromium(chromium)
    if binary is None:
        raise SystemExit(
            "No Chromium found. Pass --chromium with a path, or --html-only to stop at HTML."
        )

    with tempfile.TemporaryDirectory(prefix="pole_position_handout_") as temporary:
        source = Path(temporary) / "plc-commissioning.html"
        source.write_text(html, encoding="utf-8")
        completed = subprocess.run(
            [
                binary,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                f"--user-data-dir={temporary}/profile",
                "--virtual-time-budget=12000",
                "--run-all-compositor-stages-before-draw",
                f"--print-to-pdf={output}",
                "--no-pdf-header-footer",
                source.as_uri(),
            ],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not output.is_file():
            raise SystemExit(
                f"Chromium could not print the handout (exit {completed.returncode}).\n"
                f"{completed.stderr[-2000:]}"
            )

    print(f"{output}  {output.stat().st_size / 1024:,.0f} KB")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the standalone PLC commissioning handout."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build" / "manual" / "plc-commissioning.pdf",
        help="Where to write the PDF.",
    )
    parser.add_argument("--chromium", default=None, help="Path to a Chromium binary.")
    parser.add_argument(
        "--html-only", action="store_true", help="Write the HTML and skip rendering."
    )
    arguments = parser.parse_args()
    return build(arguments.output.resolve(), arguments.chromium, arguments.html_only)


if __name__ == "__main__":
    raise SystemExit(main())
