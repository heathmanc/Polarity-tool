"""Build the PLC interface control document as a printable PDF.

The controls engineer wiring a station needs the signal contract on paper at
the panel: what the station reads, what it writes, and -- the part that decides
how a program is written -- how long each signal holds its state.

The document reuses the handbook's stylesheet and its cycle timing diagram, so
the two cannot disagree about the sequence. Its prose is separate, because a
handbook explains and an interface specification states.

    python scripts/build_plc_icd.py --output build/manual/plc-icd.pdf

Rendering needs Chromium. The bundled Playwright browser is used when present;
otherwise pass --chromium with a path, or --html-only to stop at the HTML.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_plc_handout import (  # noqa: E402
    PRINT_CSS,
    STYLE,
    TEMPLATE as HANDBOOK,
    find_chromium,
)

ICD = ROOT / "docs" / "manual" / "plc-icd.html.in"
DIAGRAM = re.compile(r"(<svg viewBox=\"0 0 720 358\".*?</svg>)", re.S)

# Persistence badges. The register is the page a controls engineer reads first,
# and the persistence column is what they are there for, so it is given a form
# as well as a word.
ICD_CSS = """
.badge {
  display: inline-block;
  font-family: var(--display);
  font-size: 8.5pt;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
  padding: 1px 6px;
  margin-right: 6px;
  border: 1px solid currentColor;
  white-space: nowrap;
}
.badge.level { color: var(--accent); background: var(--accent-soft); }
.badge.edge { color: var(--caution); background: var(--caution-soft); }
.badge.latched { color: var(--reject); background: var(--reject-soft); }
.badge.alt { color: var(--good); background: var(--good-soft); }

.legend {
  display: grid;
  gap: 6px;
  border: 1px solid var(--rule-soft);
  border-left: 4px solid var(--accent);
  background: var(--surface);
  padding: 12px 14px;
  margin: 0 0 22px;
  font-size: 9.5pt;
  break-inside: avoid;
  page-break-inside: avoid;
}
.legend div { display: flex; gap: 4px; align-items: baseline; }

.part-num { font-size: 12pt; }
h2 { font-size: 18pt; }
"""


def assemble() -> str:
    handbook = HANDBOOK.read_text(encoding="utf-8")
    style = STYLE.search(handbook)
    diagram = DIAGRAM.search(handbook)
    if style is None:
        raise SystemExit("Could not find the handbook stylesheet.")
    if diagram is None:
        raise SystemExit(
            "Could not find the cycle timing diagram in the handbook. "
            "If its viewBox changed, update the DIAGRAM pattern here."
        )

    body = ICD.read_text(encoding="utf-8").replace("{{DIAGRAM}}", diagram.group(1))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Pole Position PLC Interface Control Document</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{style.group(1)}</style>
<style>{PRINT_CSS}</style>
<style>{ICD_CSS}</style>
</head>
<body>
<div class="sheet">
{body}
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

    with tempfile.TemporaryDirectory(prefix="pole_position_icd_") as temporary:
        source = Path(temporary) / "plc-icd.html"
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
                f"Chromium could not print the document (exit {completed.returncode}).\n"
                f"{completed.stderr[-2000:]}"
            )

    print(f"{output}  {output.stat().st_size / 1024:,.0f} KB")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the PLC interface control document."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build" / "manual" / "plc-icd.pdf",
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
