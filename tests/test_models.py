import json

import numpy as np

from battery_inspector.models import (
    InspectionCycleState,
    InspectionCycleStatus,
    InspectionDisposition,
    InspectionResult,
    LocatorSettings,
    Marking,
    MarkingClassifierSettings,
    NormalizedRect,
    Recipe,
    ReferenceCapture,
    TerminalInspection,
    TerminalFinish,
    TerminalRecipe,
    TerminalRole,
)


def test_runtime_images_make_memory_only_pass_displayable_but_not_serialized() -> None:
    image = np.full((32, 48, 3), 120, dtype=np.uint8)
    terminal = TerminalInspection(
        terminal_key="positive",
        terminal_name="Positive",
        role=TerminalRole.POSITIVE,
        expected_marking=Marking.PLUS,
        detected_marking=Marking.PLUS,
        marking_confidence=0.99,
        red_ring_expected=True,
        red_ring_detected=True,
        red_ring_confidence=0.98,
        terminal_crop_image=image,
        marking_crop_image=image,
        diagnostic_images={"overlay": image},
    )
    result = InspectionResult.create(
        recipe=None,
        disposition=InspectionDisposition.PASS,
        reason="INSPECTION PASSED",
        duration_ms=10,
        trigger_source="PLC",
        image_quality="GOOD",
        full_image_path="",
        terminals=[terminal],
        frame_id="FRAME-1",
        analysis_ready=True,
        full_image=image,
    )

    payload = result.to_dict()

    assert result.is_product_result is True
    assert "full_image" not in payload
    assert "terminal_crop_image" not in payload["terminals"][0]
    assert "diagnostic_images" not in payload["terminals"][0]
    json.dumps(payload)


def test_recipe_round_trip() -> None:
    reference = ReferenceCapture(
        capture_id="capture-1",
        path="/data/reference.png",
        sha256="abc123",
        captured_at_utc="2026-08-19T12:00:00+00:00",
        width_px=5472,
        height_px=3648,
        frame_sequence=42,
        frame_id="FRAME-42",
        camera_profile={"exposure_us": 9997.0},
        quality={"status": "GOOD"},
    )
    recipe = Recipe.new(
        name="TEST",
        part_number="123",
        description="Test recipe",
        created_by="tester",
        battery_roi=NormalizedRect(0.1, 0.2, 0.7, 0.6),
        terminals=[
            TerminalRecipe(
                key="positive",
                name="Positive",
                role=TerminalRole.POSITIVE,
                search_roi=NormalizedRect(0.7, 0.7, 0.2, 0.2),
                marking_roi=NormalizedRect(0.3, 0.3, 0.4, 0.4),
                expected_marking=Marking.PLUS,
                red_ring_required=True,
                expected_finish=TerminalFinish.SILVER,
            )
        ],
        reference_image=reference,
    )
    restored = Recipe.from_dict(recipe.to_dict())
    assert restored.name == recipe.name
    assert restored.terminals[0].expected_marking == Marking.PLUS
    assert restored.terminals[0].red_ring_required is True
    assert restored.terminals[0].expected_finish == TerminalFinish.SILVER
    assert restored.reference_image is not None
    assert restored.reference_image.capture_id == "capture-1"
    assert restored.reference_image.frame_sequence == 42
    assert restored.reference_image.camera_profile["exposure_us"] == 9997.0
    assert restored.has_reference_image is True


def test_legacy_recipe_without_terminal_finish_remains_compatible() -> None:
    terminal = TerminalRecipe(
        key="positive",
        name="Positive",
        role=TerminalRole.POSITIVE,
        search_roi=NormalizedRect(0.7, 0.7, 0.2, 0.2),
        marking_roi=NormalizedRect(0.3, 0.3, 0.4, 0.4),
        expected_marking=Marking.PLUS,
        red_ring_required=True,
    )
    payload = terminal.to_dict()
    payload.pop("expected_finish")

    restored = TerminalRecipe.from_dict(payload)

    assert restored.expected_finish == TerminalFinish.UNSPECIFIED


def test_configured_terminal_finish_is_part_of_terminal_pass() -> None:
    terminal = TerminalInspection(
        terminal_key="positive",
        terminal_name="Positive",
        role=TerminalRole.POSITIVE,
        expected_marking=Marking.PLUS,
        detected_marking=Marking.PLUS,
        marking_confidence=0.99,
        red_ring_expected=True,
        red_ring_detected=True,
        red_ring_confidence=0.99,
        expected_finish=TerminalFinish.SILVER,
        detected_finish=TerminalFinish.BRASS,
        finish_confidence=0.97,
        finish_evaluated=True,
    )

    assert terminal.marking_pass is True
    assert terminal.finish_pass is False
    assert terminal.passed is False
    assert terminal.to_dict()["finish_pass"] is False


def test_recipe_round_trip_preserves_registration_classifier_and_validation() -> None:
    recipe = Recipe.new(
        name="CONFIGURED",
        part_number="P-42",
        description="Configured recipe",
        created_by="engineer",
        battery_roi=NormalizedRect(0.1, 0.1, 0.8, 0.8),
        terminals=[
            TerminalRecipe(
                key="negative",
                name="Negative",
                role=TerminalRole.NEGATIVE,
                search_roi=NormalizedRect(0.05, 0.05, 0.2, 0.2),
                marking_roi=NormalizedRect(0.2, 0.2, 0.6, 0.6),
                expected_marking=Marking.MINUS,
                red_ring_required=False,
            )
        ],
    )
    recipe.locator_settings = LocatorSettings(
        detector="ORB",
        minimum_inliers=17,
        minimum_visible_fraction=0.91,
    )
    recipe.classifier_settings = MarkingClassifierSettings(
        method="reference_template",
        acceptance_threshold=0.66,
        minimum_margin=0.07,
    )
    recipe.validation_records = [
        {
            "disposition": "pass",
            "configuration_hash": "fingerprint",
            "cycle_id": "VALIDATE-1",
        }
    ]
    recipe.validation_configuration_hash = "fingerprint"
    recipe.validation_runs_passed = 1

    restored = Recipe.from_dict(recipe.to_dict())

    assert restored.locator_settings.detector == "ORB"
    assert restored.locator_settings.minimum_inliers == 17
    assert restored.locator_settings.minimum_visible_fraction == 0.91
    assert restored.classifier_settings.method == "reference_template"
    assert restored.classifier_settings.acceptance_threshold == 0.66
    assert restored.classifier_settings.minimum_margin == 0.07
    assert restored.validation_records[0]["cycle_id"] == "VALIDATE-1"
    assert restored.validation_configuration_hash == "fingerprint"
    assert restored.validation_runs_passed == 1


def test_reference_template_is_default_classifier_method() -> None:
    assert MarkingClassifierSettings().normalized().method == "reference_template"


def test_cycle_status_tracks_fresh_frame_identity() -> None:
    status = InspectionCycleStatus.idle()
    active = status.with_state(
        InspectionCycleState.ACQUIRING,
        "Fresh frame acquired",
        capture_id="capture-9",
        frame_id="frame-9",
        frame_sequence=9,
        captured_at_utc="2026-08-19T12:00:00+00:00",
    )

    assert active.state.active is True
    assert active.frame_id == "frame-9"
    assert active.frame_sequence == 9
    assert active.to_dict()["state"] == "acquiring"


def test_normalized_rect_clamps_to_image() -> None:
    rect = NormalizedRect(-0.2, 0.9, 0.7, 0.7).clamped()
    assert rect.x == 0.0
    assert rect.y == 0.9
    assert rect.width <= 1.0
    assert rect.height <= 0.1 + 1e-9


def test_reference_capture_blocks_explicitly_poor_quality_but_allows_legacy_unknown() -> None:
    from battery_inspector.models import ReferenceCapture

    common = {
        "capture_id": "capture-1",
        "path": "/tmp/reference.png",
        "sha256": "abc",
        "captured_at_utc": "2026-08-19T00:00:00+00:00",
        "width_px": 100,
        "height_px": 80,
    }
    poor = ReferenceCapture(**common, quality={"status": "POOR"})
    unknown = ReferenceCapture(**common)

    assert poor.quality_status == "POOR"
    assert poor.acceptable_for_recipe is False
    assert unknown.quality_status == "UNKNOWN"
    assert unknown.acceptable_for_recipe is True


def test_classifier_settings_round_trip_preserves_hybrid_controls() -> None:
    settings = MarkingClassifierSettings(
        terminal_top_minimum_confidence=0.83,
        terminal_top_conditional_minimum_confidence=0.69,
        terminal_top_conditional_geometry_confidence=0.93,
        terminal_top_conditional_minimum_center_score=0.61,
        terminal_top_conditional_minimum_inside_fraction=0.94,
        hybrid_geometry_weight=0.72,
        hybrid_minimum_template_confirmation=0.18,
        hybrid_conflict_template_threshold=0.76,
    )

    restored = MarkingClassifierSettings.from_dict(settings.to_dict()).normalized()

    assert restored.terminal_top_minimum_confidence == 0.83
    assert restored.terminal_top_conditional_minimum_confidence == 0.69
    assert restored.terminal_top_conditional_geometry_confidence == 0.93
    assert restored.terminal_top_conditional_minimum_center_score == 0.61
    assert restored.terminal_top_conditional_minimum_inside_fraction == 0.94
    assert restored.hybrid_geometry_weight == 0.72
    assert restored.hybrid_minimum_template_confirmation == 0.18
    assert restored.hybrid_conflict_template_threshold == 0.76


def test_classifier_settings_migrate_v081_geometry_confidence_key() -> None:
    restored = MarkingClassifierSettings.from_dict(
        {
            "terminal_top_conditional_minimum_geometry_confidence": 0.94,
            "terminal_top_conditional_maximum_center_offset_fraction": 0.42,
            "terminal_top_conditional_minimum_inside_fraction": 0.96,
        }
    )

    assert restored.terminal_top_conditional_geometry_confidence == 0.94
    # The old center-offset gate has no one-to-one equivalent in the v0.9
    # centered-stamp score.  Loading uses the current conservative default.
    assert restored.terminal_top_conditional_minimum_center_score == 0.55
    assert restored.terminal_top_conditional_minimum_inside_fraction == 0.96


def test_persisted_settings_ignore_unknown_future_keys() -> None:
    classifier = MarkingClassifierSettings.from_dict(
        {
            "acceptance_threshold": 0.63,
            "future_classifier_setting": "not-known-by-this-build",
        }
    )
    locator = LocatorSettings.from_dict(
        {
            "minimum_inliers": 19,
            "future_locator_setting": 123,
        }
    )

    assert classifier.acceptance_threshold == 0.63
    assert locator.minimum_inliers == 19


def test_classifier_settings_current_key_wins_over_legacy_alias() -> None:
    restored = MarkingClassifierSettings.from_dict(
        {
            "terminal_top_conditional_geometry_confidence": 0.92,
            "terminal_top_conditional_minimum_geometry_confidence": 0.77,
        }
    )

    assert restored.terminal_top_conditional_geometry_confidence == 0.92


def test_classifier_settings_round_trip_preserves_onnx_model_binding() -> None:
    settings = MarkingClassifierSettings(
        method="onnx_ml",
        ml_model_id="polarity-v1",
        ml_model_version="2026.08.20",
        ml_model_sha256="a" * 64,
        ml_minimum_confidence=0.93,
        ml_minimum_margin=0.17,
        ml_center_fallback_minimum_confidence=0.97,
        ml_center_fallback_minimum_margin=0.29,
        ml_test_time_quadrants=False,
    )
    restored = MarkingClassifierSettings.from_dict(settings.to_dict()).normalized()
    assert restored.method == "onnx_ml"
    assert restored.ml_model_id == "polarity-v1"
    assert restored.ml_model_version == "2026.08.20"
    assert restored.ml_model_sha256 == "a" * 64
    assert restored.ml_minimum_confidence == 0.93
    assert restored.ml_minimum_margin == 0.17
    assert restored.ml_center_fallback_minimum_confidence == 0.97
    assert restored.ml_center_fallback_minimum_margin == 0.29
    assert restored.ml_test_time_quadrants is False
