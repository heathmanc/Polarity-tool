from __future__ import annotations

from pathlib import Path

import cv2

from battery_inspector.models import MarkingClassifierSettings
from battery_inspector.services.vision import TerminalFaceValidator


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "terminal_face"


def _image(name: str):
    image = cv2.imread(str(FIXTURES / name), cv2.IMREAD_COLOR)
    assert image is not None
    return image


def test_reference_terminal_face_gate_accepts_real_present_negative() -> None:
    validator = TerminalFaceValidator()
    result = validator.validate(
        _image("valid_negative_current.png"),
        _image("valid_negative_reference.png"),
        MarkingClassifierSettings(),
    )

    assert result.present is True
    assert result.status == "TERMINAL_FACE_PRESENT"
    assert result.confidence >= 0.80
    assert result.metrics["terminal_face_anomaly_count"] <= 1
    assert result.metrics["terminal_face_radial_gate_passed"] is True
    assert "terminal_face_overlay" in result.diagnostic_images


def test_reference_terminal_face_gate_accepts_real_present_positive() -> None:
    validator = TerminalFaceValidator()
    result = validator.validate(
        _image("valid_positive_current.png"),
        _image("valid_positive_reference.png"),
        MarkingClassifierSettings(),
    )

    assert result.present is True
    assert result.status == "TERMINAL_FACE_PRESENT"
    assert result.confidence >= 0.90
    assert result.metrics["terminal_face_anomaly_count"] <= 1


def test_reference_terminal_face_gate_rejects_open_missing_terminal_face() -> None:
    """Regression for the field screenshot that falsely passed as MINUS 99.4%."""

    validator = TerminalFaceValidator()
    result = validator.validate(
        _image("missing_terminal_face.png"),
        _image("valid_negative_reference.png"),
        MarkingClassifierSettings(),
    )

    assert result.present is False
    assert result.status == "TERMINAL_FACE_MISSING"
    assert result.confidence >= 0.90
    assert result.metrics["terminal_face_anomaly_count"] >= 2
    assert result.metrics["terminal_face_radial_gate_passed"] is False
    assert result.metrics["terminal_face_center_color_gate_passed"] is False
    assert "terminal_face_overlay" in result.diagnostic_images
    assert "terminal_face_compare" in result.diagnostic_images


def test_terminal_face_gate_requires_multiple_anomaly_families() -> None:
    """A single tunable anomaly is not enough to declare a face missing."""

    settings = MarkingClassifierSettings(
        terminal_face_minimum_structure_correlation=0.99,
    )
    validator = TerminalFaceValidator()
    result = validator.validate(
        _image("valid_negative_current.png"),
        _image("valid_negative_reference.png"),
        settings,
    )

    assert result.metrics["terminal_face_structure_gate_passed"] is False
    assert result.metrics["terminal_face_anomaly_count"] == 1
    assert result.present is True


def test_pipeline_bypasses_polarity_classifier_when_terminal_face_is_missing(tmp_path: Path) -> None:
    import numpy as np

    from battery_inspector.evidence import reference_capture_from_file
    from battery_inspector.models import (
        InspectionDisposition,
        Marking,
        NormalizedRect,
        Recipe,
        TerminalRecipe,
        TerminalRole,
    )
    from battery_inspector.services.vision import (
        BatteryLocation,
        BatteryLocator,
        InspectionPipeline,
        MarkingClassification,
        MarkingClassifier,
    )

    reference = _image("valid_negative_reference.png")
    current = _image("missing_terminal_face.png")
    reference = cv2.resize(reference, (640, 640), interpolation=cv2.INTER_AREA)
    current = cv2.resize(current, (640, 640), interpolation=cv2.INTER_AREA)
    reference_path = tmp_path / "reference.png"
    assert cv2.imwrite(str(reference_path), reference)

    terminal = TerminalRecipe(
        key="negative",
        name="Negative terminal",
        role=TerminalRole.NEGATIVE,
        search_roi=NormalizedRect(0.0, 0.0, 1.0, 1.0),
        marking_roi=NormalizedRect(0.0, 0.0, 1.0, 1.0),
        expected_marking=Marking.MINUS,
        red_ring_required=False,
    )
    recipe = Recipe.new(
        name="FACE-GATE-REGRESSION",
        part_number="FACE-001",
        description="Missing terminal-face regression",
        battery_roi=NormalizedRect(0.0, 0.0, 1.0, 1.0),
        terminals=[terminal],
        created_by="test",
    )
    recipe.reference_image = reference_capture_from_file(
        reference_path,
        source="RECIPE_WIZARD",
        camera_backend="pypylon",
        camera_description="Test camera",
    )

    class IdentityLocator(BatteryLocator):
        ready = True
        status = "IDENTITY_TEST_LOCATOR"

        def locate(self, image, recipe):
            del recipe
            height, width = image.shape[:2]
            return BatteryLocation(
                aligned_battery=image.copy(),
                reference_battery=reference.copy(),
                battery_roi=NormalizedRect(0.0, 0.0, 1.0, 1.0),
                battery_polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                reference_to_current=np.eye(3, dtype=np.float64),
                reference_image_size=(width, height),
                current_image_size=(width, height),
                reference_battery_bounds=(0, 0, width, height),
                metrics={"method": self.status},
            )

    class SpyClassifier(MarkingClassifier):
        ready = True
        status = "SPY_CLASSIFIER"

        def __init__(self) -> None:
            self.calls = 0

        def classify(self, marking_crop, terminal, recipe, reference_battery):
            del marking_crop, terminal, recipe, reference_battery
            self.calls += 1
            return MarkingClassification(
                marking=Marking.MINUS,
                confidence=0.999,
                evaluated=True,
                status="SHOULD_NOT_RUN",
            )

    classifier = SpyClassifier()
    pipeline = InspectionPipeline(
        output_directory=tmp_path / "evidence",
        battery_locator=IdentityLocator(),
        marking_classifier=classifier,
    )
    result = pipeline.inspect(
        current,
        recipe,
        trigger_source="TEST",
        cycle_id="CYCLE-MISSING-FACE",
        validation_mode=True,
    )

    assert classifier.calls == 0
    assert result.disposition == InspectionDisposition.REJECT
    assert result.reason == "TERMINAL FACE MISSING"
    assert result.analysis_ready is True
    assert len(result.terminals) == 1
    terminal_result = result.terminals[0]
    assert terminal_result.terminal_face_evaluated is True
    assert terminal_result.terminal_face_present is False
    assert terminal_result.terminal_face_status == "TERMINAL_FACE_MISSING"
    assert terminal_result.marking_evaluated is False
    assert terminal_result.passed is False
    assert "terminal_face_overlay" in terminal_result.diagnostic_image_paths
