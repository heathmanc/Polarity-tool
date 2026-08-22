from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from battery_inspector.build_info import INSPECTION_ENGINE
from battery_inspector.data import RecipeRepository
from battery_inspector.evidence import reference_capture_from_file
from battery_inspector.models import (
    InspectionDisposition,
    Marking,
    NormalizedRect,
    Recipe,
    TerminalFinish,
    TerminalRecipe,
    TerminalRole,
)
from battery_inspector.services.vision import (
    InspectionPipeline,
    MarkingClassification,
    MarkingClassifier,
    RedRingDetector,
    ReferenceFeatureBatteryLocator,
    ReferenceTemplateMarkingClassifier,
    TerminalFinishValidation,
    rect_within,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PROJECT_ROOT / "battery_inspector" / "assets"


class _InvalidMarkingClassifier(MarkingClassifier):
    ready = True
    status = "TEST_INVALID_MARKING"

    def classify(self, marking_crop, terminal, recipe, reference_battery):
        del marking_crop, terminal, recipe, reference_battery
        return MarkingClassification(
            marking=Marking.INVALID_MARKING,
            confidence=0.99,
            evaluated=True,
            class_scores={"invalid_marking": 0.99},
            metrics={"ml_model_id": "unit-four-class"},
            status="ML_CLASSIFIED_INVALID_MARKING",
            note="Unit classifier selected invalid marking.",
        )


class _MismatchFinishValidator:
    status = "TEST_TERMINAL_FINISH_VALIDATOR"

    def validate(self, current, reference, expected):
        del current, reference
        detected = (
            TerminalFinish.BRASS
            if expected == TerminalFinish.SILVER
            else TerminalFinish.SILVER
        )
        return TerminalFinishValidation(
            detected=detected,
            confidence=0.99,
            evaluated=True,
            status="TERMINAL_FINISH_MISMATCH",
            note="Injected terminal-finish mismatch.",
        )


def _seeded_recipe(tmp_path: Path) -> Recipe:
    repository = RecipeRepository(tmp_path / "recipes.db")
    repository.seed_demo_data(ASSETS / "demo_reference_good.png")
    recipe = repository.get_active_recipe()
    assert recipe is not None
    assert recipe.has_reference_image
    return recipe


def _real_reference_recipe(
    tmp_path: Path,
    *,
    expected_matches_demo: bool,
    validation_complete: bool = True,
) -> Recipe:
    recipe = _seeded_recipe(tmp_path)
    reference_path = tmp_path / "captured-reference.png"
    shutil.copy2(ASSETS / "demo_battery.jpg", reference_path)
    recipe.reference_image = reference_capture_from_file(
        reference_path,
        source="RECIPE_WIZARD",
        camera_backend="pypylon",
        camera_description="Basler test camera",
    )
    if expected_matches_demo:
        for terminal in recipe.terminals:
            if terminal.role == TerminalRole.NEGATIVE:
                terminal.expected_marking = Marking.PLUS
            elif terminal.role == TerminalRole.POSITIVE:
                terminal.expected_marking = Marking.MINUS
    if validation_complete:
        recipe.validation_configuration_hash = "test-validated"
        recipe.validation_records = [
            {
                "disposition": "pass",
                "configuration_hash": recipe.validation_configuration_hash,
                "inspection_engine": INSPECTION_ENGINE,
                "cycle_id": f"TEST-VALIDATION-{index + 1}",
            }
            for index in range(recipe.validation_runs_required)
        ]
        recipe.validation_runs_passed = recipe.validation_runs_required
    else:
        recipe.validation_runs_passed = 0
        recipe.validation_records = []
        recipe.validation_configuration_hash = ""
    return recipe


def _known_good_reference_for_default_recipe(tmp_path: Path, recipe: Recipe) -> Path:
    """Create a test-only known-good reference by swapping the visible sample stamps.

    The supplied production sample contains a plus on the physical negative terminal
    and a minus on the physical positive terminal. Swapping only those two marking
    patches creates a deterministic reference with the recipe's expected MINUS/PLUS
    arrangement while preserving the rest of the battery for registration.
    """

    image = cv2.imread(str(ASSETS / "demo_battery.jpg"), cv2.IMREAD_COLOR)
    assert image is not None
    height, width = image.shape[:2]
    rectangles: dict[TerminalRole, tuple[int, int, int, int]] = {}
    for terminal in recipe.terminals:
        terminal_full = rect_within(recipe.battery_roi, terminal.search_roi)
        marking_full = rect_within(terminal_full, terminal.marking_roi)
        rectangles[terminal.role] = (
            round(marking_full.x * width),
            round(marking_full.y * height),
            round((marking_full.x + marking_full.width) * width),
            round((marking_full.y + marking_full.height) * height),
        )
    negative = rectangles[TerminalRole.NEGATIVE]
    positive = rectangles[TerminalRole.POSITIVE]
    negative_crop = image[negative[1] : negative[3], negative[0] : negative[2]].copy()
    positive_crop = image[positive[1] : positive[3], positive[0] : positive[2]].copy()
    reference = image.copy()
    reference[negative[1] : negative[3], negative[0] : negative[2]] = cv2.resize(
        positive_crop,
        (negative[2] - negative[0], negative[3] - negative[1]),
    )
    reference[positive[1] : positive[3], positive[0] : positive[2]] = cv2.resize(
        negative_crop,
        (positive[2] - positive[0], positive[3] - positive[1]),
    )
    path = tmp_path / "known-good-reference.png"
    assert cv2.imwrite(str(path), reference)
    return path


def test_red_ring_detector_distinguishes_sample_terminals() -> None:
    detector = RedRingDetector()
    negative = cv2.imread(str(ASSETS / "negative_terminal.jpg"))
    positive = cv2.imread(str(ASSETS / "positive_terminal.jpg"))
    negative_present, _ = detector.detect(negative)
    positive_present, _ = detector.detect(positive)
    assert negative_present is False
    assert positive_present is True


def test_bundled_known_good_demo_reference_rejects_the_reversed_fixture(tmp_path: Path) -> None:
    recipe = _seeded_recipe(tmp_path)
    image = cv2.imread(str(ASSETS / "demo_battery.jpg"))
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(image, recipe, trigger_source="TEST", cycle_id="CYCLE-DEMO")

    assert recipe.reference_is_demo is True
    assert result.disposition == InspectionDisposition.REJECT
    assert result.passed is False
    assert result.analysis_ready is True
    assert result.reason == "POLARITY MARKINGS REVERSED"
    assert result.readiness_issues == []
    assert result.locator_metrics["terminal_regions_excluded_from_pose"] == 2
    negative = next(item for item in result.terminals if item.role == TerminalRole.NEGATIVE)
    positive = next(item for item in result.terminals if item.role == TerminalRole.POSITIVE)
    assert negative.detected_marking == Marking.PLUS
    assert positive.detected_marking == Marking.MINUS
    assert Path(result.full_image_path).is_file()
    assert Path(result.manifest_path).is_file()
    assert all(item.diagnostic_image_paths for item in result.terminals)
    assert all(
        Path(path).is_file()
        for item in result.terminals
        for path in item.diagnostic_image_paths.values()
    )
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 8
    assert manifest["software"]["inspection_engine"] == (
        "reference_registration_terminal_face_guard_ml_v2"
    )


def test_bundled_known_good_demo_reference_passes_the_known_good_fixture(
    tmp_path: Path,
) -> None:
    recipe = _seeded_recipe(tmp_path)
    image = cv2.imread(str(ASSETS / "demo_reference_good.png"))
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(image, recipe, trigger_source="TEST", cycle_id="CYCLE-GOOD")

    assert result.disposition == InspectionDisposition.PASS
    assert result.analysis_ready is True
    assert result.is_product_result is True
    assert isinstance(result.full_image, np.ndarray)
    assert result.full_image_path == ""
    assert result.evidence_directory == ""
    assert result.manifest_path == ""
    assert all(item.classification_status == "CLASS_ACCEPTED" for item in result.terminals)
    assert all(item.diagnostic_images for item in result.terminals)
    assert all(not item.diagnostic_image_paths for item in result.terminals)
    assert not (tmp_path / "evidence" / "inspections").exists()


def test_invalid_marking_prediction_is_an_explicit_product_reject(
    tmp_path: Path,
) -> None:
    recipe = _seeded_recipe(tmp_path)
    image = cv2.imread(str(ASSETS / "demo_reference_good.png"))
    pipeline = InspectionPipeline(
        output_directory=tmp_path / "evidence",
        marking_classifier=_InvalidMarkingClassifier(),
    )

    result = pipeline.inspect(
        image,
        recipe,
        trigger_source="TEST",
        cycle_id="CYCLE-INVALID-MARKING",
    )

    assert result.disposition == InspectionDisposition.REJECT
    assert result.reason == "INVALID MARKING"
    assert result.passed is False
    assert all(
        item.detected_marking == Marking.INVALID_MARKING
        for item in result.terminals
    )
    assert all(not item.marking_pass for item in result.terminals)


def test_wrong_terminal_finish_is_an_explicit_product_reject(tmp_path: Path) -> None:
    recipe = _seeded_recipe(tmp_path)
    for terminal in recipe.terminals:
        terminal.expected_finish = TerminalFinish.SILVER
    image = cv2.imread(str(ASSETS / "demo_reference_good.png"))
    pipeline = InspectionPipeline(
        output_directory=tmp_path / "evidence",
        terminal_finish_validator=_MismatchFinishValidator(),
    )

    result = pipeline.inspect(
        image,
        recipe,
        trigger_source="TEST",
        cycle_id="CYCLE-FINISH-MISMATCH",
    )

    assert result.disposition == InspectionDisposition.REJECT
    assert result.reason == "TERMINAL FINISH MISMATCH"
    assert result.passed is False
    assert all(item.marking_pass for item in result.terminals)
    assert all(not item.finish_pass for item in result.terminals)


def test_validation_mode_runs_real_locator_and_classifier(tmp_path: Path) -> None:
    recipe = _real_reference_recipe(
        tmp_path,
        expected_matches_demo=True,
        validation_complete=False,
    )
    image = cv2.imread(str(ASSETS / "demo_battery.jpg"))
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(
        image,
        recipe,
        trigger_source="RECIPE_VALIDATION",
        cycle_id="CYCLE-VALIDATE",
        validation_mode=True,
    )

    assert result.disposition == InspectionDisposition.PASS
    assert result.analysis_ready is True
    assert result.readiness_issues == []
    assert result.locator_status == "REFERENCE_FEATURE_HOMOGRAPHY"
    assert result.classifier_status == "ROTATION_INVARIANT_HYBRID_V2_1"
    assert result.locator_metrics["inliers"] >= 12
    assert len(result.battery_polygon) == 4
    assert Path(result.aligned_battery_path).is_file()
    assert Path(result.manifest_path).is_file()
    assert all(item.diagnostic_image_paths for item in result.terminals)
    assert all(item.marking_evaluated for item in result.terminals)
    assert all(item.ring_evaluated for item in result.terminals)


def test_production_cycle_requires_completed_real_validation(tmp_path: Path) -> None:
    recipe = _real_reference_recipe(
        tmp_path,
        expected_matches_demo=True,
        validation_complete=False,
    )
    image = cv2.imread(str(ASSETS / "demo_battery.jpg"))
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(image, recipe, trigger_source="MANUAL", cycle_id="CYCLE-NOT-VALIDATED")

    assert result.disposition == InspectionDisposition.NOT_READY
    assert result.reason == "RECIPE VALIDATION REQUIRED"
    assert any(item.startswith("RECIPE_VALIDATION_REQUIRED") for item in result.readiness_issues)


def test_reference_registration_tracks_rotation_translation_and_scale(tmp_path: Path) -> None:
    recipe = _real_reference_recipe(
        tmp_path,
        expected_matches_demo=True,
        validation_complete=True,
    )
    reference = cv2.imread(str(ASSETS / "demo_battery.jpg"))
    height, width = reference.shape[:2]
    transform = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), 14.0, 0.96)
    transform[:, 2] += np.array([35.0, -12.0])
    current = cv2.warpAffine(
        reference,
        transform,
        (width, height),
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(20, 20, 20),
    )
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(current, recipe, trigger_source="TEST", cycle_id="CYCLE-ROTATED")

    assert result.disposition == InspectionDisposition.PASS
    assert result.analysis_ready is True
    assert result.locator_metrics["inlier_ratio"] > 0.75
    assert result.locator_metrics["median_reprojection_error_px"] < 2.0
    assert abs(abs(result.locator_metrics["rotation_deg"]) - 14.0) < 1.0
    assert abs(result.locator_metrics["scale"] - 0.96) < 0.03
    assert len(result.battery_polygon) == 4
    assert all(len(item.terminal_polygon) == 4 for item in result.terminals)
    assert all(len(item.marking_polygon) == 4 for item in result.terminals)


def test_real_template_classifier_rejects_reversed_sample(tmp_path: Path) -> None:
    recipe = _seeded_recipe(tmp_path)
    known_good = _known_good_reference_for_default_recipe(tmp_path, recipe)
    recipe.reference_image = reference_capture_from_file(
        known_good,
        source="RECIPE_WIZARD",
        camera_backend="pypylon",
    )
    recipe.validation_runs_passed = recipe.validation_runs_required
    recipe.validation_configuration_hash = "validated-default-layout"
    recipe.validation_records = [
        {
            "disposition": "pass",
            "configuration_hash": recipe.validation_configuration_hash,
            "inspection_engine": INSPECTION_ENGINE,
            "cycle_id": f"TEST-VALIDATION-{index + 1}",
        }
        for index in range(recipe.validation_runs_required)
    ]
    current = cv2.imread(str(ASSETS / "demo_battery.jpg"))
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(current, recipe, trigger_source="TEST", cycle_id="CYCLE-REVERSED")

    assert result.disposition == InspectionDisposition.REJECT
    assert result.reason == "POLARITY MARKINGS REVERSED"
    assert result.analysis_ready is True
    negative = next(item for item in result.terminals if item.role == TerminalRole.NEGATIVE)
    positive = next(item for item in result.terminals if item.role == TerminalRole.POSITIVE)
    assert negative.detected_marking == Marking.PLUS
    assert positive.detected_marking == Marking.MINUS
    assert negative.marking_pass is False
    assert positive.marking_pass is False
    assert negative.ring_pass is True
    assert positive.ring_pass is True
    assert negative.class_scores["plus"] > negative.class_scores["minus"]
    assert positive.class_scores["minus"] > positive.class_scores["plus"]


def test_no_active_recipe_never_passes_and_still_saves_fresh_frame(tmp_path: Path) -> None:
    image = cv2.imread(str(ASSETS / "demo_battery.jpg"))
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(image, None, trigger_source="MANUAL", cycle_id="CYCLE-NO-RECIPE")

    assert result.disposition == InspectionDisposition.NOT_READY
    assert result.reason == "NO ACTIVE RECIPE"
    assert result.recipe_id == ""
    assert result.terminals == []
    assert Path(result.full_image_path).is_file()


def test_missing_recipe_reference_file_is_not_ready(tmp_path: Path) -> None:
    recipe = _real_reference_recipe(
        tmp_path,
        expected_matches_demo=True,
        validation_complete=True,
    )
    assert recipe.reference_image is not None
    recipe = replace(
        recipe,
        reference_image=replace(
            recipe.reference_image,
            path=str(tmp_path / "missing-reference.png"),
        ),
    )
    image = cv2.imread(str(ASSETS / "demo_battery.jpg"))
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(
        image,
        recipe,
        trigger_source="TEST",
        cycle_id="CYCLE-MISSING-REFERENCE",
    )

    assert result.disposition == InspectionDisposition.NOT_READY
    assert result.reason == "RECIPE REFERENCE IMAGE FILE MISSING"
    assert "RECIPE_REFERENCE_FILE_MISSING" in result.readiness_issues
    assert result.analysis_ready is False
    assert Path(result.full_image_path).is_file()
    assert Path(result.manifest_path).is_file()
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 8
    assert manifest["software"]["inspection_engine"] == (
        "reference_registration_terminal_face_guard_ml_v2"
    )


def test_locator_and_classifier_report_recipe_readiness(tmp_path: Path) -> None:
    recipe = _real_reference_recipe(
        tmp_path,
        expected_matches_demo=True,
        validation_complete=False,
    )
    locator = ReferenceFeatureBatteryLocator()
    classifier = ReferenceTemplateMarkingClassifier()
    assert locator.readiness_issues(recipe) == []
    assert classifier.readiness_issues(recipe) == []


def test_reference_registration_handles_a_180_degree_battery_rotation(tmp_path: Path) -> None:
    recipe = _real_reference_recipe(
        tmp_path,
        expected_matches_demo=True,
        validation_complete=True,
    )
    reference = cv2.imread(str(ASSETS / "demo_battery.jpg"), cv2.IMREAD_COLOR)
    current = cv2.rotate(reference, cv2.ROTATE_180)
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(current, recipe, trigger_source="TEST", cycle_id="CYCLE-180")

    assert result.disposition == InspectionDisposition.PASS
    assert result.analysis_ready is True
    assert abs(abs(result.locator_metrics["rotation_deg"]) - 180.0) < 1.0
    assert result.locator_metrics["orientation_margin"] > 0.20
    assert all(item.passed for item in result.terminals)


def test_180_degree_rotation_does_not_normalize_a_reversed_product_into_a_pass(
    tmp_path: Path,
) -> None:
    recipe = _seeded_recipe(tmp_path)
    reversed_fixture = cv2.imread(str(ASSETS / "demo_battery.jpg"), cv2.IMREAD_COLOR)
    current = cv2.rotate(reversed_fixture, cv2.ROTATE_180)
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(current, recipe, trigger_source="TEST", cycle_id="CYCLE-180-BAD")

    assert result.disposition == InspectionDisposition.REJECT
    assert result.reason == "POLARITY MARKINGS REVERSED"
    assert abs(abs(float(result.locator_metrics["rotation_deg"])) - 180.0) < 1.0
    assert float(result.locator_metrics["orientation_margin"]) > 0.20
    assert result.locator_metrics["terminal_regions_excluded_from_pose"] == 2


def test_unrelated_well_exposed_image_is_a_product_reject_not_a_pass(tmp_path: Path) -> None:
    recipe = _real_reference_recipe(
        tmp_path,
        expected_matches_demo=True,
        validation_complete=True,
    )
    rng = np.random.default_rng(20260819)
    unrelated = rng.integers(35, 220, size=(1200, 1800, 3), dtype=np.uint8)
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(
        unrelated,
        recipe,
        trigger_source="TEST",
        cycle_id="CYCLE-NO-BATTERY",
    )

    assert result.disposition == InspectionDisposition.REJECT
    assert result.reason == "BATTERY COULD NOT BE LOCATED"
    assert result.analysis_ready is True
    assert result.passed is False
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["registration_error"]


def test_reference_template_classifier_supports_plus_and_blank_recipes() -> None:
    rng = np.random.default_rng(12)
    height, width = 320, 760
    reference_battery = np.full((height, width, 3), 125, dtype=np.uint8)
    terminals = [
        TerminalRecipe(
            key="positive",
            name="Positive",
            role=TerminalRole.POSITIVE,
            search_roi=NormalizedRect(0.08, 0.15, 0.34, 0.70),
            marking_roi=NormalizedRect(0.20, 0.20, 0.60, 0.60),
            expected_marking=Marking.PLUS,
            red_ring_required=False,
        ),
        TerminalRecipe(
            key="negative",
            name="Negative",
            role=TerminalRole.NEGATIVE,
            search_roi=NormalizedRect(0.58, 0.15, 0.34, 0.70),
            marking_roi=NormalizedRect(0.20, 0.20, 0.60, 0.60),
            expected_marking=Marking.BLANK,
            red_ring_required=False,
        ),
    ]
    for terminal in terminals:
        rect = terminal.search_roi
        x1 = int(rect.x * width)
        y1 = int(rect.y * height)
        x2 = int((rect.x + rect.width) * width)
        y2 = int((rect.y + rect.height) * height)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        radius = min(x2 - x1, y2 - y1) // 2 - 8
        texture = np.full((y2 - y1, x2 - x1, 3), 180, dtype=np.uint8)
        noise = rng.normal(0, 12, texture.shape[:2]).astype(np.int16)
        texture = np.clip(texture.astype(np.int16) + noise[..., None], 0, 255).astype(
            np.uint8
        )
        mask = np.zeros(texture.shape[:2], dtype=np.uint8)
        cv2.circle(mask, ((x2 - x1) // 2, (y2 - y1) // 2), radius, 255, -1)
        roi = reference_battery[y1:y2, x1:x2]
        roi[mask > 0] = texture[mask > 0]
        cv2.circle(reference_battery, (cx, cy), radius, (90, 90, 90), 3, cv2.LINE_AA)
        if terminal.expected_marking == Marking.PLUS:
            cv2.line(reference_battery, (cx - 35, cy), (cx + 35, cy), (65, 65, 65), 10)
            cv2.line(reference_battery, (cx, cy - 35), (cx, cy + 35), (65, 65, 65), 10)

    recipe = Recipe.new(
        name="PLUS_BLANK",
        part_number="PB-1",
        description="Synthetic plus/blank classifier test",
        created_by="test",
        battery_roi=NormalizedRect(0.0, 0.0, 1.0, 1.0),
        terminals=terminals,
    )
    classifier = ReferenceTemplateMarkingClassifier()
    assert classifier.readiness_issues(recipe, reference_battery) == []

    for terminal in terminals:
        terminal_crop = reference_battery[
            round(terminal.search_roi.y * height) : round(
                (terminal.search_roi.y + terminal.search_roi.height) * height
            ),
            round(terminal.search_roi.x * width) : round(
                (terminal.search_roi.x + terminal.search_roi.width) * width
            ),
        ]
        mark_rect = terminal.marking_roi
        crop_height, crop_width = terminal_crop.shape[:2]
        marking_crop = terminal_crop[
            round(mark_rect.y * crop_height) : round(
                (mark_rect.y + mark_rect.height) * crop_height
            ),
            round(mark_rect.x * crop_width) : round(
                (mark_rect.x + mark_rect.width) * crop_width
            ),
        ]
        shifted = cv2.warpAffine(
            marking_crop,
            np.float32([[1.0, 0.0, 2.0], [0.0, 1.0, -1.0]]),
            (marking_crop.shape[1], marking_crop.shape[0]),
            borderMode=cv2.BORDER_REFLECT,
        )
        outcome = classifier.classify(shifted, terminal, recipe, reference_battery)
        assert outcome.marking == terminal.expected_marking
        assert outcome.confidence >= recipe.classifier_settings.acceptance_threshold


def test_successful_validation_crops_are_added_as_recipe_templates(tmp_path: Path) -> None:
    recipe = _real_reference_recipe(
        tmp_path,
        expected_matches_demo=True,
        validation_complete=False,
    )
    image = cv2.imread(str(ASSETS / "demo_battery.jpg"), cv2.IMREAD_COLOR)
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")
    first = pipeline.inspect(
        image,
        recipe,
        trigger_source="RECIPE_VALIDATION",
        cycle_id="VALIDATION-TEMPLATE-1",
        validation_mode=True,
    )
    assert first.disposition == InspectionDisposition.PASS

    recipe.validation_configuration_hash = "configuration-1"
    recipe.validation_records = [
        {
            "disposition": "pass",
            "configuration_hash": "configuration-1",
            "inspection_engine": INSPECTION_ENGINE,
            "terminals": [item.to_dict() for item in first.terminals],
        }
    ]
    second = pipeline.inspect(
        image,
        recipe,
        trigger_source="RECIPE_VALIDATION",
        cycle_id="VALIDATION-TEMPLATE-2",
        validation_mode=True,
    )

    assert second.disposition == InspectionDisposition.PASS
    assert all(
        int(item.classification_metrics.get("template_count", 0)) >= 4
        for item in second.terminals
    )


def test_reference_template_classifier_requires_two_taught_classes(tmp_path: Path) -> None:
    recipe = _seeded_recipe(tmp_path)
    for terminal in recipe.terminals:
        terminal.expected_marking = Marking.PLUS
    classifier = ReferenceTemplateMarkingClassifier()

    issues = classifier.readiness_issues(recipe)

    assert any(
        item.startswith("POLARITY_CLASSIFIER_NEEDS_TWO_TAUGHT_CLASSES")
        for item in issues
    )


def test_flat_marking_regions_are_unreadable_and_never_pass(tmp_path: Path) -> None:
    recipe = _seeded_recipe(tmp_path)
    image = cv2.imread(str(ASSETS / "demo_reference_good.png"), cv2.IMREAD_COLOR)
    assert image is not None
    height, width = image.shape[:2]
    for terminal in recipe.terminals:
        terminal_full = rect_within(recipe.battery_roi, terminal.search_roi)
        marking_full = rect_within(terminal_full, terminal.marking_roi)
        x1 = round(marking_full.x * width)
        y1 = round(marking_full.y * height)
        x2 = round((marking_full.x + marking_full.width) * width)
        y2 = round((marking_full.y + marking_full.height) * height)
        image[y1:y2, x1:x2] = 128
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(
        image,
        recipe,
        trigger_source="TEST",
        cycle_id="CYCLE-UNREADABLE",
    )

    assert result.disposition == InspectionDisposition.REJECT
    # v0.15 rejects grossly destroyed/flat terminal-face evidence before the
    # marking classifier is allowed to invent a polarity decision.
    assert result.reason == "TERMINAL FACE INVALID"
    assert all(item.detected_marking == Marking.UNREADABLE for item in result.terminals)
    assert result.passed is False


def test_validation_from_previous_inspection_engine_is_not_accepted(
    tmp_path: Path,
) -> None:
    recipe = _real_reference_recipe(
        tmp_path,
        expected_matches_demo=True,
        validation_complete=True,
    )
    for record in recipe.validation_records:
        record["inspection_engine"] = "reference_registration_template_v1"
    image = cv2.imread(str(ASSETS / "demo_battery.jpg"), cv2.IMREAD_COLOR)
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")

    result = pipeline.inspect(
        image,
        recipe,
        trigger_source="TEST",
        cycle_id="CYCLE-OLD-ENGINE-VALIDATION",
    )

    assert result.disposition == InspectionDisposition.NOT_READY
    assert result.reason == "RECIPE VALIDATION REQUIRED"
    assert any(
        item.startswith("RECIPE_VALIDATION_REQUIRED:0/")
        and item.endswith(INSPECTION_ENGINE)
        for item in result.readiness_issues
    )


def test_validation_capture_runs_and_saves_crops_when_ml_classifier_is_not_ready(tmp_path: Path) -> None:
    recipe = _real_reference_recipe(
        tmp_path,
        expected_matches_demo=True,
        validation_complete=False,
    )
    recipe.classifier_settings.method = "onnx_ml"
    recipe.classifier_settings.ml_model_id = "missing-model"
    recipe.classifier_settings.ml_model_version = "1"
    recipe.classifier_settings.ml_model_sha256 = "1" * 64
    for terminal in recipe.terminals:
        terminal.marking_roi_shape = "circle"

    image = cv2.imread(str(ASSETS / "demo_battery.jpg"))
    pipeline = InspectionPipeline(output_directory=tmp_path / "evidence")
    result = pipeline.inspect(
        image,
        recipe,
        trigger_source="RECIPE_VALIDATION",
        cycle_id="CYCLE-VALIDATE-NO-ML",
        validation_mode=True,
    )

    assert result.disposition == InspectionDisposition.NOT_READY
    assert result.terminals
    assert all(Path(item.marking_crop_path).is_file() for item in result.terminals)
    assert all(item.marking_evaluated is False for item in result.terminals)
    assert all(item.classification_status == "CLASSIFIER_NOT_READY" for item in result.terminals)
