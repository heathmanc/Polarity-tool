from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from battery_inspector.data import RecipeRepository  # noqa: E402
from battery_inspector.models import InspectionDisposition  # noqa: E402
from battery_inspector.services.vision import InspectionPipeline  # noqa: E402

ASSETS = PROJECT_ROOT / "battery_inspector" / "assets"


def _result_summary(result: Any) -> dict[str, Any]:
    return {
        "disposition": result.disposition.value,
        "reason": result.reason,
        "cycle_id": result.cycle_id,
        "manifest_path": result.manifest_path,
        "registration": {
            key: result.locator_metrics.get(key)
            for key in (
                "detector",
                "good_matches",
                "inliers",
                "inlier_ratio",
                "median_reprojection_error_px",
                "scale",
                "rotation_deg",
                "visible_fraction",
                "orientation_margin",
                "orientation_corrected_180",
            )
        },
        "terminals": [
            {
                "key": terminal.terminal_key,
                "expected": terminal.expected_marking.value,
                "detected": terminal.detected_marking.value,
                "confidence": terminal.marking_confidence,
                "red_ring_expected": terminal.red_ring_expected,
                "red_ring_detected": terminal.red_ring_detected,
                "marking_pass": terminal.marking_pass,
                "ring_pass": terminal.ring_pass,
                "classifier_status": terminal.classification_status,
            }
            for terminal in result.terminals
        ],
    }


def run_smoke(output_directory: Path) -> dict[str, Any]:
    output_directory.mkdir(parents=True, exist_ok=True)
    repository = RecipeRepository(output_directory / "smoke-recipes.db")
    # Deliberately exercise the default seed path. It must select the bundled
    # known-good reference, never the intentionally reversed live demo frame.
    repository.seed_demo_data()
    recipe = repository.get_active_recipe()
    if recipe is None or recipe.reference_image is None:
        raise RuntimeError("Bundled active recipe/reference could not be created")

    known_good = cv2.imread(str(ASSETS / "demo_reference_good.png"), cv2.IMREAD_COLOR)
    reversed_fixture = cv2.imread(str(ASSETS / "demo_battery.jpg"), cv2.IMREAD_COLOR)
    if known_good is None or reversed_fixture is None:
        raise RuntimeError("Bundled vision smoke-test images could not be opened")

    pipeline = InspectionPipeline(output_directory=output_directory)
    cases = {
        "known_good": pipeline.inspect(
            known_good,
            recipe,
            trigger_source="VISION_SMOKE_GOOD",
            cycle_id="SMOKE-GOOD",
        ),
        "reversed": pipeline.inspect(
            reversed_fixture,
            recipe,
            trigger_source="VISION_SMOKE_REVERSED",
            cycle_id="SMOKE-REVERSED",
        ),
        "reversed_rotated_180": pipeline.inspect(
            cv2.rotate(reversed_fixture, cv2.ROTATE_180),
            recipe,
            trigger_source="VISION_SMOKE_REVERSED_180",
            cycle_id="SMOKE-REVERSED-180",
        ),
    }

    failures: list[str] = []
    if cases["known_good"].disposition != InspectionDisposition.PASS:
        failures.append(
            "known-good fixture did not PASS: "
            f"{cases['known_good'].disposition.value} / {cases['known_good'].reason}"
        )
    for name in ("reversed", "reversed_rotated_180"):
        result = cases[name]
        if result.disposition != InspectionDisposition.REJECT:
            failures.append(
                f"{name} fixture was not rejected: "
                f"{result.disposition.value} / {result.reason}"
            )
        if result.reason != "POLARITY MARKINGS REVERSED":
            failures.append(
                f"{name} fixture returned the wrong reason: {result.reason}"
            )

    return {
        "status": "FAIL" if failures else "PASS",
        "recipe": {
            "name": recipe.name,
            "revision": recipe.revision,
            "reference_path": recipe.reference_image.path,
            "reference_source": recipe.reference_image.source,
        },
        "cases": {name: _result_summary(result) for name, result in cases.items()},
        "failures": failures,
        "output_directory": str(output_directory.resolve()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the bundled non-GUI polarity regression: known-good must pass, "
            "and reversed markings must reject even after a 180-degree rotation."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Keep smoke-test evidence in this directory instead of a temporary directory.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optionally write the JSON result to a file.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.output_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="polarity-vision-smoke-")
        output_directory = Path(temporary.name)
    else:
        run_name = (
            "smoke-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid4().hex[:8]
        )
        output_directory = args.output_dir / run_name

    try:
        summary = run_smoke(output_directory)
        rendered = json.dumps(summary, indent=2, sort_keys=True)
        print(rendered)
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(rendered + "\n", encoding="utf-8")
        return 0 if summary["status"] == "PASS" else 1
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
