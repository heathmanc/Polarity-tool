from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from battery_inspector.models import (
    Marking,
    MarkingClassifierSettings,
    NormalizedRect,
    Recipe,
    RecipeStatus,
    TerminalRecipe,
    TerminalRole,
)
from battery_inspector.services.vision import (
    RedRingDetector,
    ReferenceTemplateMarkingClassifier,
)

FIXTURE = Path(__file__).parent / "fixtures" / "cycle_000011"


def _image(name: str) -> np.ndarray:
    image = cv2.imread(str(FIXTURE / name), cv2.IMREAD_COLOR)
    assert image is not None
    return image


def _cycle_recipe_and_reference() -> tuple[Recipe, np.ndarray, list[TerminalRecipe]]:
    negative_reference = _image("negative_reference.png")
    positive_reference = _image("positive_reference.png")

    # Construct a deterministic synthetic reference battery whose two terminal
    # search halves contain the exact known-good marking crops retained from the
    # real cycle.  The classifier still crops through the normal recipe path.
    height = 700
    width = 1600
    half_width = width // 2
    reference_battery = np.full((height, width, 3), 80, dtype=np.uint8)

    negative_x = (half_width - negative_reference.shape[1]) // 2
    negative_y = (height - negative_reference.shape[0]) // 2
    positive_x = half_width + (half_width - positive_reference.shape[1]) // 2
    positive_y = (height - positive_reference.shape[0]) // 2

    reference_battery[
        negative_y : negative_y + negative_reference.shape[0],
        negative_x : negative_x + negative_reference.shape[1],
    ] = negative_reference
    reference_battery[
        positive_y : positive_y + positive_reference.shape[0],
        positive_x : positive_x + positive_reference.shape[1],
    ] = positive_reference

    terminals = [
        TerminalRecipe(
            key="negative",
            name="Negative Terminal",
            role=TerminalRole.NEGATIVE,
            search_roi=NormalizedRect(0.0, 0.0, 0.5, 1.0),
            marking_roi=NormalizedRect(
                negative_x / half_width,
                negative_y / height,
                negative_reference.shape[1] / half_width,
                negative_reference.shape[0] / height,
            ),
            expected_marking=Marking.MINUS,
            red_ring_required=False,
        ),
        TerminalRecipe(
            key="positive",
            name="Positive Terminal",
            role=TerminalRole.POSITIVE,
            search_roi=NormalizedRect(0.5, 0.0, 0.5, 1.0),
            marking_roi=NormalizedRect(
                (positive_x - half_width) / half_width,
                positive_y / height,
                positive_reference.shape[1] / half_width,
                positive_reference.shape[0] / height,
            ),
            expected_marking=Marking.PLUS,
            red_ring_required=True,
        ),
    ]
    now = datetime.now(timezone.utc).isoformat()
    recipe = Recipe(
        recipe_id="cycle-000011",
        recipe_number=11,
        name="Cycle 000011 regression",
        part_number="fixture",
        description="Conditional terminal-top acceptance regression",
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


def test_cycle_000011_correct_battery_uses_conditional_positive_top_and_passes() -> None:
    recipe, reference_battery, terminals = _cycle_recipe_and_reference()
    classifier = ReferenceTemplateMarkingClassifier()
    ring_detector = RedRingDetector()

    negative = classifier.classify(
        _image("negative_current.png"), terminals[0], recipe, reference_battery
    )
    positive = classifier.classify(
        _image("positive_current.png"), terminals[1], recipe, reference_battery
    )
    negative_ring, _ = ring_detector.detect(_image("negative_terminal.png"))
    positive_ring, _ = ring_detector.detect(_image("positive_terminal.png"))

    assert classifier.status == "ROTATION_INVARIANT_HYBRID_V2_1"
    assert negative.marking == Marking.MINUS
    assert positive.marking == Marking.PLUS
    assert negative.status == "HYBRID_CLASS_ACCEPTED"
    assert positive.status == "HYBRID_CLASS_ACCEPTED"
    assert negative.metrics["terminal_top_acceptance"] == "NOMINAL"
    assert positive.metrics["terminal_top_acceptance"] == "CONDITIONAL"
    assert positive.metrics["terminal_top_conditionally_accepted"] is True
    assert positive.metrics["terminal_top_used"] is True
    assert positive.metrics["decision_mode"] == "rotation_invariant_hybrid"
    assert 0.68 <= positive.metrics["terminal_top_detection_confidence"] < 0.80
    assert positive.metrics["geometry_marking"] == Marking.PLUS.value
    assert positive.metrics["geometry_confidence"] >= 0.90
    assert positive.metrics["geometry_template_confirmation"] >= 0.12
    assert positive.confidence >= 0.70
    assert "conditional" in positive.note.lower()

    assert negative.marking == terminals[0].expected_marking
    assert positive.marking == terminals[1].expected_marking
    assert negative_ring is terminals[0].red_ring_required
    assert positive_ring is terminals[1].red_ring_required


def _geometry(
    *,
    marking: Marking = Marking.PLUS,
    confidence: float = 0.96,
    status: str = "TWO_PERPENDICULAR_LINES",
    top_confidence: float = 0.74,
    method: str = "HOUGH_CIRCLE",
    inside_fraction: float = 1.0,
    center_score: float = 0.76,
) -> SimpleNamespace:
    metrics = {
        "terminal_top_detection_method": method,
        "terminal_top_detection_confidence": top_confidence,
        "terminal_top_inside_fraction": inside_fraction,
        "plus_gate": marking == Marking.PLUS,
        "minus_gate": marking == Marking.MINUS,
        "plus_center_score": center_score,
        "minus_center_score": center_score,
    }
    return SimpleNamespace(
        metrics=metrics,
        marking=marking,
        confidence=confidence,
        status=status,
    )


def test_conditional_gate_requires_real_hough_circle() -> None:
    settings = MarkingClassifierSettings().normalized()
    accepted, mode, reason = ReferenceTemplateMarkingClassifier._terminal_top_acceptance(
        _geometry(method="CENTER_FALLBACK"),
        settings,
        terminal_top_available=True,
    )

    assert accepted is False
    assert mode == "REJECTED"
    assert reason == "CENTER_FALLBACK_NOT_ALLOWED"


def test_conditional_gate_does_not_relax_blank_detection() -> None:
    settings = MarkingClassifierSettings().normalized()
    accepted, mode, reason = ReferenceTemplateMarkingClassifier._terminal_top_acceptance(
        _geometry(
            marking=Marking.BLANK,
            confidence=0.98,
            status="NO_DOMINANT_STAMP_LINE",
        ),
        settings,
        terminal_top_available=True,
    )

    assert accepted is False
    assert mode == "REJECTED"
    assert reason == "CONDITIONAL_GATE_REQUIRES_PLUS_OR_MINUS"


def test_conditional_gate_rejects_weak_or_partially_visible_geometry() -> None:
    settings = MarkingClassifierSettings().normalized()

    weak = ReferenceTemplateMarkingClassifier._terminal_top_acceptance(
        _geometry(confidence=0.82),
        settings,
        terminal_top_available=True,
    )
    clipped = ReferenceTemplateMarkingClassifier._terminal_top_acceptance(
        _geometry(inside_fraction=0.60),
        settings,
        terminal_top_available=True,
    )

    assert weak == (
        False,
        "REJECTED",
        "GEOMETRY_CONFIDENCE_BELOW_CONDITIONAL_GATE",
    )
    assert clipped == (
        False,
        "REJECTED",
        "TERMINAL_TOP_NOT_FULLY_VISIBLE",
    )
