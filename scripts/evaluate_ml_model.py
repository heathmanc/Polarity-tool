from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from battery_inspector.services.ml import (  # noqa: E402
    REQUIRED_POLARITY_CLASSES,
    OnnxPolarityModel,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the deployed ONNX classifier on a class-folder challenge/test "
            "set using the production fail-closed confidence/margin gates. "
            "Quadrant TTA is optional and disabled by default."
        )
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=PROJECT_ROOT / "models" / "polarity_classifier.onnx",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PROJECT_ROOT / "models" / "polarity_classifier.json",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=PROJECT_ROOT / "dataset" / "polarity_cls" / "test",
    )
    parser.add_argument("--minimum-confidence", type=float, default=0.90)
    parser.add_argument("--minimum-margin", type=float, default=0.15)
    parser.add_argument(
        "--tta",
        action="store_true",
        help="Enable optional 0/90/180/270 degree test-time averaging.",
    )
    parser.add_argument("--no-tta", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    model = OnnxPolarityModel(args.model, args.manifest)
    info = model.info(require_runtime=True)
    if not info.get("ready"):
        print(json.dumps(info, indent=2, sort_keys=True))
        return 2

    minimum_confidence = min(0.999, max(0.0, float(args.minimum_confidence)))
    minimum_margin = min(0.99, max(0.0, float(args.minimum_margin)))
    labels = list(REQUIRED_POLARITY_CLASSES)
    confusion: dict[str, dict[str, int]] = {
        actual: {predicted: 0 for predicted in labels + ["low_confidence"]}
        for actual in labels
    }
    per_class_total: dict[str, int] = defaultdict(int)
    accepted_count = 0
    correct_accepted = 0
    total_count = 0
    unreadable_files: list[str] = []

    tta_quadrants = bool(args.tta and not args.no_tta)

    for actual in labels:
        folder = args.data / actual
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                unreadable_files.append(str(path))
                continue
            inference = model.infer(image, tta_quadrants=tta_quadrants)
            total_count += 1
            per_class_total[actual] += 1
            if (
                inference.confidence < minimum_confidence
                or inference.margin < minimum_margin
                or inference.top_label not in labels
            ):
                predicted = "low_confidence"
            else:
                predicted = inference.top_label
                accepted_count += 1
                if predicted == actual:
                    correct_accepted += 1
            confusion[actual][predicted] += 1

    per_class: dict[str, dict[str, float | int]] = {}
    correct_total = 0
    for actual in labels:
        total = per_class_total[actual]
        correct = confusion[actual][actual]
        correct_total += correct
        per_class[actual] = {
            "count": total,
            "correct": correct,
            "recall_with_abstentions": (correct / total) if total else 0.0,
            "low_confidence": confusion[actual]["low_confidence"],
        }

    summary = {
        "schema_version": 1,
        "model": info,
        "data": str(args.data.resolve()),
        "minimum_confidence": minimum_confidence,
        "minimum_margin": minimum_margin,
        "tta_quadrants": tta_quadrants,
        "total_images": total_count,
        "accepted_images": accepted_count,
        "acceptance_rate": accepted_count / total_count if total_count else 0.0,
        "correct_all_images": correct_total,
        "accuracy_with_abstentions": correct_total / total_count if total_count else 0.0,
        "accepted_accuracy": correct_accepted / accepted_count if accepted_count else 0.0,
        "per_class": per_class,
        "confusion": confusion,
        "unreadable_files": unreadable_files,
    }
    text = json.dumps(summary, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if total_count else 3


if __name__ == "__main__":
    raise SystemExit(main())
