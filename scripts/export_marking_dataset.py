from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from battery_inspector.dataset import export_marking_dataset  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export known-good PLUS/MINUS/BLANK isolated terminal-top crops from "
            "recipe validation evidence for ML training. Recent evidence uses "
            "terminal_top.png so the red ring/case symbol are excluded. "
            "INVALID_MARKING examples must be labeled manually in the guided HMI."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PROJECT_ROOT / "runtime",
        help="Station runtime data directory (default: ./runtime)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dataset" / "markings",
        help="Output dataset directory (default: ./dataset/markings)",
    )
    parser.add_argument(
        "--include-production-passes",
        action="store_true",
        help="Also include crops from production cycles that passed all checks.",
    )
    parser.add_argument(
        "--allow-legacy-marking-crops",
        action="store_true",
        help=(
            "Allow older marking_crop_path evidence when terminal_top.png is missing. "
            "Use only after manually verifying those crops do not expose the red ring "
            "or molded case polarity symbols."
        ),
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the output directory before exporting.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = export_marking_dataset(
        args.data_dir,
        args.output,
        include_production_passes=args.include_production_passes,
        allow_legacy_marking_crops=args.allow_legacy_marking_crops,
        clean=args.clean,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
