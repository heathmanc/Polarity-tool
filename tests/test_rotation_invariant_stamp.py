from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from battery_inspector.models import (
    Marking,
    MarkingClassifierSettings,
    NormalizedRect,
    Recipe,
    RecipeStatus,
    TerminalRecipe,
    TerminalRole,
)
from battery_inspector.services.markings import GeometricStampClassifier
from battery_inspector.services.vision import (
    RedRingDetector,
    ReferenceTemplateMarkingClassifier,
)

FIXTURE = Path(__file__).parent / "fixtures" / "cycle_000006"


def _image(name: str) -> np.ndarray:
    image = cv2.imread(str(FIXTURE / name), cv2.IMREAD_COLOR)
    assert image is not None
    return image


def _cycle_recipe_and_reference() -> tuple[Recipe, np.ndarray, list[TerminalRecipe]]:
    negative_reference = _image("negative_reference.png")
    positive_reference = _image("positive_reference.png")
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
    recipe = Recipe(
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
        created_by="test",
        created_at_utc=now,
        updated_by="test",
        updated_at_utc=now,
        classifier_settings=MarkingClassifierSettings(),
    )
    return recipe, reference_battery, terminals


def _periodic_angle_difference(first: float, second: float, period: float) -> float:
    return abs(((first - second + period / 2.0) % period) - period / 2.0)


def test_cycle_000006_classifies_both_rotated_stamps_correctly() -> None:
    recipe, reference_battery, terminals = _cycle_recipe_and_reference()
    classifier = ReferenceTemplateMarkingClassifier()

    negative = classifier.classify(
        _image("negative_current.png"), terminals[0], recipe, reference_battery
    )
    positive = classifier.classify(
        _image("positive_current.png"), terminals[1], recipe, reference_battery
    )

    assert classifier.status == "ROTATION_INVARIANT_HYBRID_V2_1"
    assert negative.marking == Marking.MINUS
    assert positive.marking == Marking.PLUS
    assert negative.status == "HYBRID_CLASS_ACCEPTED"
    assert positive.status == "HYBRID_CLASS_ACCEPTED"
    assert negative.confidence >= 0.80
    assert positive.confidence >= 0.80
    assert negative.metrics["terminal_top_used"] is True
    assert positive.metrics["terminal_top_used"] is True
    assert negative.metrics["decision_mode"] == "rotation_invariant_hybrid"
    assert positive.metrics["decision_mode"] == "rotation_invariant_hybrid"
    assert set(negative.diagnostic_images) >= {
        "terminal_top",
        "stamp_overlay",
        "stamp_response",
        "canonical_stamp",
    }


def test_cycle_000006_negative_reference_has_large_independent_stamp_rotation() -> None:
    classifier = GeometricStampClassifier()
    current = classifier.classify(_image("negative_current.png"))
    reference = classifier.classify(_image("negative_reference.png"))

    assert current.marking == Marking.MINUS
    assert reference.marking == Marking.MINUS
    current_angle = float(current.metrics["stamp_angle_deg"])
    reference_angle = float(reference.metrics["stamp_angle_deg"])
    assert _periodic_angle_difference(current_angle, reference_angle, 180.0) >= 40.0


@pytest.mark.parametrize("angle", [0, 37, 91, 143])
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("negative_current.png", Marking.MINUS),
        ("positive_current.png", Marking.PLUS),
    ],
)
def test_geometric_stamp_is_rotation_invariant(
    filename: str,
    expected: Marking,
    angle: int,
) -> None:
    image = _image(filename)
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
    rotated = cv2.warpAffine(
        image,
        matrix,
        (width, height),
        borderMode=cv2.BORDER_REFLECT_101,
    )

    result = GeometricStampClassifier().classify(rotated)

    assert result.marking == expected
    assert result.confidence >= 0.70
    assert result.metrics["terminal_top_detection_method"] == "HOUGH_CIRCLE"


def test_cycle_000006_ring_checks_and_polarity_resolve_to_pass() -> None:
    recipe, reference_battery, terminals = _cycle_recipe_and_reference()
    classifier = ReferenceTemplateMarkingClassifier()
    ring_detector = RedRingDetector()

    negative_marking = classifier.classify(
        _image("negative_current.png"), terminals[0], recipe, reference_battery
    )
    positive_marking = classifier.classify(
        _image("positive_current.png"), terminals[1], recipe, reference_battery
    )
    negative_ring, _ = ring_detector.detect(_image("negative_terminal.png"))
    positive_ring, _ = ring_detector.detect(_image("positive_terminal.png"))

    assert negative_marking.marking == terminals[0].expected_marking
    assert positive_marking.marking == terminals[1].expected_marking
    assert negative_ring is terminals[0].red_ring_required
    assert positive_ring is terminals[1].red_ring_required


def test_cycle_000006_swapped_markings_are_detected_independently() -> None:
    recipe, reference_battery, terminals = _cycle_recipe_and_reference()
    classifier = ReferenceTemplateMarkingClassifier()

    observed_on_negative = classifier.classify(
        _image("positive_current.png"), terminals[0], recipe, reference_battery
    )
    observed_on_positive = classifier.classify(
        _image("negative_current.png"), terminals[1], recipe, reference_battery
    )

    assert observed_on_negative.marking == Marking.PLUS
    assert observed_on_positive.marking == Marking.MINUS
    assert observed_on_negative.marking != terminals[0].expected_marking
    assert observed_on_positive.marking != terminals[1].expected_marking
