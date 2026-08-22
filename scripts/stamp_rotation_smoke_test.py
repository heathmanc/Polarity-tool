from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from battery_inspector.models import (  # noqa: E402
    Marking,
    MarkingClassifierSettings,
    NormalizedRect,
    Recipe,
    RecipeStatus,
    TerminalRecipe,
    TerminalRole,
)
from battery_inspector.services.vision import (  # noqa: E402
    RedRingDetector,
    ReferenceTemplateMarkingClassifier,
)

FIXTURE = ROOT / "tests" / "fixtures" / "cycle_000006"


def _read(name: str) -> np.ndarray:
    path = FIXTURE / name
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"Could not read regression fixture: {path}")
    return image


def _recipe() -> tuple[Recipe, np.ndarray, list[TerminalRecipe]]:
    negative_reference = _read("negative_reference.png")
    positive_reference = _read("positive_reference.png")
    reference_battery = np.full((500, 1000, 3), 80, dtype=np.uint8)
    reference_battery[100:401, 100:401] = negative_reference
    reference_battery[47:453, 547:953] = positive_reference

    terminals = [
        TerminalRecipe(
            key="negative",
            name="Negative Terminal",
            role=TerminalRole.NEGATIVE,
            search_roi=NormalizedRect(0.0, 0.0, 0.5, 1.0),
            marking_roi=NormalizedRect(0.2, 0.2, 0.602, 0.602),
            expected_marking=Marking.MINUS,
            red_ring_required=False,
        ),
        TerminalRecipe(
            key="positive",
            name="Positive Terminal",
            role=TerminalRole.POSITIVE,
            search_roi=NormalizedRect(0.5, 0.0, 0.5, 1.0),
            marking_roi=NormalizedRect(0.094, 0.094, 0.812, 0.812),
            expected_marking=Marking.PLUS,
            red_ring_required=True,
        ),
    ]
    now = datetime.now(timezone.utc).isoformat()
    return (
        Recipe(
            recipe_id="cycle-000006",
            recipe_number=6,
            name="Cycle 000006 regression",
            part_number="fixture",
            description="Independent terminal-head rotation regression",
            revision=1,
            status=RecipeStatus.DRAFT,
            battery_roi=NormalizedRect(0.0, 0.0, 1.0, 1.0),
            orientation_reference="case_geometry",
            terminals=terminals,
            created_by="smoke-test",
            created_at_utc=now,
            updated_by="smoke-test",
            updated_at_utc=now,
            classifier_settings=MarkingClassifierSettings(),
        ),
        reference_battery,
        terminals,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the exact cycle-000006 regression that previously false-rejected "
            "a rotated MINUS stamp."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the operator summary.",
    )
    args = parser.parse_args()

    recipe, reference_battery, terminals = _recipe()
    classifier = ReferenceTemplateMarkingClassifier()
    ring_detector = RedRingDetector()

    current_files = {
        "negative": "negative_current.png",
        "positive": "positive_current.png",
    }
    terminal_files = {
        "negative": "negative_terminal.png",
        "positive": "positive_terminal.png",
    }

    payload: dict[str, object] = {
        "classifier": classifier.status,
        "fixture": str(FIXTURE),
        "terminals": {},
    }
    overall = True
    for terminal in terminals:
        classification = classifier.classify(
            _read(current_files[terminal.key]),
            terminal,
            recipe,
            reference_battery,
        )
        ring_present, ring_confidence = ring_detector.detect(
            _read(terminal_files[terminal.key])
        )
        marking_pass = classification.marking == terminal.expected_marking
        ring_pass = ring_present is terminal.red_ring_required
        terminal_pass = marking_pass and ring_pass
        overall = overall and terminal_pass
        payload["terminals"][terminal.key] = {
            "expected_marking": terminal.expected_marking.value,
            "detected_marking": classification.marking.value,
            "confidence": classification.confidence,
            "status": classification.status,
            "stamp_angle_deg": classification.metrics.get("stamp_angle_deg"),
            "terminal_top_confidence": classification.metrics.get(
                "terminal_top_detection_confidence"
            ),
            "decision_mode": classification.metrics.get("decision_mode"),
            "red_ring_expected": terminal.red_ring_required,
            "red_ring_detected": ring_present,
            "red_ring_confidence": ring_confidence,
            "pass": terminal_pass,
        }

    payload["overall"] = "PASS" if overall else "FAIL"

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Cycle 000006 rotation-invariant stamping regression")
        print(f"Classifier: {classifier.status}")
        for key, result in payload["terminals"].items():
            print(
                f"{key.upper():8s}  expected={result['expected_marking'].upper():5s}  "
                f"detected={result['detected_marking'].upper():5s}  "
                f"confidence={result['confidence']:.1%}  "
                f"stamp-angle={float(result['stamp_angle_deg']):.1f} deg  "
                f"top-lock={float(result['terminal_top_confidence']):.1%}  "
                f"ring={'YES' if result['red_ring_detected'] else 'NO'}  "
                f"result={'PASS' if result['pass'] else 'FAIL'}"
            )
        print(f"Overall smoke-test status: {payload['overall']}")

    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
