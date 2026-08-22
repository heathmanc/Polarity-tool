from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from battery_inspector.dataset import prepare_classification_dataset  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare train/val/test folders for the PLUS/MINUS/BLANK/"
            "INVALID_MARKING "
            "terminal-top classifier."
        )
    )
    parser.add_argument(
        "--exported",
        type=Path,
        default=PROJECT_ROOT / "dataset" / "markings",
        help="Flat dataset created by export_marking_dataset.py",
    )
    parser.add_argument(
        "--manual",
        type=Path,
        default=PROJECT_ROOT / "dataset" / "manual",
        help=(
            "Optional manually-labeled PLUS/MINUS/BLANK/INVALID_MARKING "
            "class folders"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dataset" / "polarity_cls",
        help="Classification dataset root containing train/val/test",
    )
    parser.add_argument("--val-fraction", type=float, default=0.15)
    parser.add_argument("--test-fraction", type=float, default=0.15)
    parser.add_argument("--clean", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = prepare_classification_dataset(
        args.exported,
        args.output,
        manual_directory=args.manual,
        validation_fraction=args.val_fraction,
        test_fraction=args.test_fraction,
        clean=args.clean,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
