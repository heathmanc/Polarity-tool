from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from battery_inspector.models import (
    Marking,
    MarkingClassifierSettings,
    NormalizedRect,
    Recipe,
    TerminalRecipe,
    TerminalRole,
)
from battery_inspector.ml_training import MlTrainingParameters
from battery_inspector.services.ml import OnnxPolarityModel
from battery_inspector.services.vision import OnnxMlMarkingClassifier


FIXTURE = Path(__file__).parent / "fixtures" / "cycle_000011" / "positive_current.png"


class _Tensor:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSession:
    def __init__(self, logits: tuple[float, ...]) -> None:
        self.logits = np.asarray(logits, dtype=np.float32)

    def get_inputs(self):
        return [_Tensor("images")]

    def get_outputs(self):
        return [_Tensor("output0")]

    def run(self, output_names, feed):
        assert output_names == ["output0"]
        batch = np.asarray(feed["images"])
        return [np.tile(self.logits, (batch.shape[0], 1))]


def _package(
    tmp_path: Path,
    logits=(5.0, 1.0, 0.0),
    *,
    classes=("plus", "minus", "blank"),
) -> OnnxPolarityModel:
    model_path = tmp_path / "polarity_classifier.onnx"
    model_path.write_bytes(b"fake-onnx-model-for-unit-test")
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    manifest_path = tmp_path / "polarity_classifier.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": "unit-polarity",
                "model_version": "1.2.3",
                "classes": list(classes),
                "input_size": [224, 224],
                "model_sha256": digest,
                "source": "unit-test",
                "preprocess": {
                    "color_order": "RGB",
                    "scale": 1.0 / 255.0,
                    "mean": [0.0, 0.0, 0.0],
                    "std": [1.0, 1.0, 1.0],
                },
            }
        ),
        encoding="utf-8",
    )
    return OnnxPolarityModel(
        model_path,
        manifest_path,
        session_factory=lambda _path: _FakeSession(logits),
    )


def test_onnx_model_package_verifies_hash_and_runs_tta(tmp_path: Path) -> None:
    model = _package(tmp_path)
    info = model.info(require_runtime=True)
    assert info["ready"] is True
    assert info["model_id"] == "unit-polarity"
    image = np.full((180, 180, 3), 127, dtype=np.uint8)
    inference = model.infer(image, tta_quadrants=True)
    assert inference.tta_count == 4
    assert inference.top_label == "plus"
    assert inference.confidence > 0.95
    assert inference.margin > 0.80


def test_new_ml_training_and_recipe_defaults_do_not_enable_tta() -> None:
    assert MlTrainingParameters().tta_quadrants is False
    assert MarkingClassifierSettings().ml_test_time_quadrants is False


def test_onnx_model_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    model = _package(tmp_path)
    model.model_path.write_bytes(b"tampered")
    fresh = OnnxPolarityModel(
        model.model_path,
        model.manifest_path,
        session_factory=lambda _path: _FakeSession((5.0, 1.0, 0.0)),
    )
    issues = fresh.readiness_issues(require_runtime=False)
    assert issues
    assert "SHA-256" in issues[0]


def test_ml_classifier_binds_recipe_to_exact_model_and_classifies_plus(tmp_path: Path) -> None:
    model = _package(tmp_path)
    info = model.info(require_runtime=True)
    classifier = OnnxMlMarkingClassifier(model)
    terminal = TerminalRecipe(
        key="positive",
        name="Positive",
        role=TerminalRole.POSITIVE,
        search_roi=NormalizedRect(0.6, 0.6, 0.2, 0.2),
        marking_roi=NormalizedRect(0.2, 0.2, 0.6, 0.6),
        expected_marking=Marking.PLUS,
        red_ring_required=True,
    )
    recipe = Recipe.new(
        name="ML TEST",
        part_number="ML-1",
        description="",
        created_by="test",
        battery_roi=NormalizedRect(0.1, 0.1, 0.8, 0.8),
        terminals=[terminal],
    )
    recipe.classifier_settings = MarkingClassifierSettings(
        method="onnx_ml",
        ml_model_id=str(info["model_id"]),
        ml_model_version=str(info["model_version"]),
        ml_model_sha256=str(info["model_sha256"]),
        ml_minimum_confidence=0.80,
        ml_minimum_margin=0.10,
    )
    assert classifier.readiness_issues(recipe) == []
    crop = cv2.imread(str(FIXTURE), cv2.IMREAD_COLOR)
    assert crop is not None
    outcome = classifier.classify(crop, terminal, recipe, np.zeros((10, 10, 3), np.uint8))
    assert outcome.evaluated is True
    assert outcome.marking == Marking.PLUS
    assert outcome.status == "ML_CLASS_ACCEPTED"
    assert outcome.confidence > 0.95
    assert outcome.metrics["ml_model_id"] == "unit-polarity"
    assert "terminal_top" in outcome.diagnostic_images


def test_ml_recipe_model_change_requires_revalidation(tmp_path: Path) -> None:
    model = _package(tmp_path)
    classifier = OnnxMlMarkingClassifier(model)
    terminal = TerminalRecipe(
        key="positive",
        name="Positive",
        role=TerminalRole.POSITIVE,
        search_roi=NormalizedRect(0.6, 0.6, 0.2, 0.2),
        marking_roi=NormalizedRect(0.2, 0.2, 0.6, 0.6),
        expected_marking=Marking.PLUS,
        red_ring_required=True,
    )
    recipe = Recipe.new(
        name="ML OLD",
        part_number="ML-OLD",
        description="",
        created_by="test",
        battery_roi=NormalizedRect(0.1, 0.1, 0.8, 0.8),
        terminals=[terminal],
    )
    recipe.classifier_settings = MarkingClassifierSettings(
        method="onnx_ml",
        ml_model_id="different-model",
        ml_model_version="1",
        ml_model_sha256="0" * 64,
    )
    issues = classifier.readiness_issues(recipe)
    assert any("ML_MODEL_ID_CHANGED" in item for item in issues)


def test_legacy_unreadable_model_remains_loadable_for_existing_recipe(tmp_path: Path) -> None:
    model = _package(
        tmp_path,
        logits=(5.0, 1.0, 0.0, -1.0),
        classes=("plus", "minus", "blank", "unreadable"),
    )
    info = model.info(require_runtime=True)
    assert info["ready"] is True


def test_ml_classifier_accepts_invalid_marking_class_as_explicit_reject(tmp_path: Path) -> None:
    model = _package(
        tmp_path,
        logits=(0.0, 0.0, 0.0, 6.0),
        classes=("plus", "minus", "blank", "invalid_marking"),
    )
    info = model.info(require_runtime=True)
    assert info["ready"] is True
    classifier = OnnxMlMarkingClassifier(model)
    terminal = TerminalRecipe(
        key="positive",
        name="Positive",
        role=TerminalRole.POSITIVE,
        search_roi=NormalizedRect(0.6, 0.6, 0.2, 0.2),
        marking_roi=NormalizedRect(0.2, 0.2, 0.6, 0.6),
        expected_marking=Marking.PLUS,
        red_ring_required=True,
    )
    recipe = Recipe.new(
        name="ML INVALID",
        part_number="ML-INVALID",
        description="",
        created_by="test",
        battery_roi=NormalizedRect(0.1, 0.1, 0.8, 0.8),
        terminals=[terminal],
    )
    recipe.classifier_settings = MarkingClassifierSettings(
        method="onnx_ml",
        ml_model_id=str(info["model_id"]),
        ml_model_version=str(info["model_version"]),
        ml_model_sha256=str(info["model_sha256"]),
        ml_minimum_confidence=0.80,
        ml_minimum_margin=0.10,
    )
    crop = cv2.imread(str(FIXTURE), cv2.IMREAD_COLOR)
    assert crop is not None
    outcome = classifier.classify(
        crop, terminal, recipe, np.zeros((10, 10, 3), np.uint8)
    )
    assert outcome.evaluated is True
    assert outcome.marking == Marking.INVALID_MARKING
    assert outcome.status == "ML_CLASSIFIED_INVALID_MARKING"
    assert "invalid marking" in outcome.note.lower()


def test_onnx_runtime_self_test_rejects_wrong_class_count(tmp_path: Path) -> None:
    model = _package(tmp_path, logits=(4.0, 1.0), classes=("plus", "minus", "blank"))
    issues = model.readiness_issues(require_runtime=True)
    assert issues
    assert "class count" in issues[0].lower()


def test_ml_classifier_uses_taught_circle_without_hough_search(tmp_path: Path) -> None:
    model = _package(tmp_path)
    info = model.info(require_runtime=True)
    classifier = OnnxMlMarkingClassifier(model)
    terminal = TerminalRecipe(
        key="positive",
        name="Positive",
        role=TerminalRole.POSITIVE,
        search_roi=NormalizedRect(0.6, 0.6, 0.2, 0.2),
        marking_roi=NormalizedRect(0.2, 0.2, 0.6, 0.6),
        expected_marking=Marking.PLUS,
        red_ring_required=True,
        marking_roi_shape="circle",
    )
    recipe = Recipe.new(
        name="ML CIRCLE",
        part_number="ML-CIRCLE",
        description="",
        created_by="test",
        battery_roi=NormalizedRect(0.1, 0.1, 0.8, 0.8),
        terminals=[terminal],
    )
    recipe.classifier_settings = MarkingClassifierSettings(
        method="onnx_ml",
        ml_model_id=str(info["model_id"]),
        ml_model_version=str(info["model_version"]),
        ml_model_sha256=str(info["model_sha256"]),
        ml_minimum_confidence=0.80,
        ml_minimum_margin=0.10,
    )
    crop = np.full((240, 240, 3), 150, dtype=np.uint8)
    cv2.line(crop, (90, 120), (150, 120), (20, 20, 20), 10)
    cv2.line(crop, (120, 90), (120, 150), (20, 20, 20), 10)
    outcome = classifier.classify(crop, terminal, recipe, np.zeros((10, 10, 3), np.uint8))
    assert outcome.marking == Marking.PLUS
    assert outcome.metrics["ml_terminal_top_method"] == "TAUGHT_CIRCLE_DIRECT"
    assert outcome.metrics["marking_roi_shape"] == "circle"
    assert outcome.metrics["ml_center_fallback"] is False


def test_onnx_inference_defaults_to_single_exact_crop(tmp_path: Path) -> None:
    model = _package(tmp_path)
    image = np.full((120, 180, 3), 127, dtype=np.uint8)
    inference = model.infer(image)
    assert inference.tta_count == 1


def test_ml_classifier_legacy_rectangle_uses_exact_crop_without_hough(tmp_path: Path) -> None:
    model = _package(tmp_path)
    info = model.info(require_runtime=True)
    classifier = OnnxMlMarkingClassifier(model)
    terminal = TerminalRecipe(
        key="positive",
        name="Positive",
        role=TerminalRole.POSITIVE,
        search_roi=NormalizedRect(0.6, 0.6, 0.2, 0.2),
        marking_roi=NormalizedRect(0.2, 0.2, 0.6, 0.6),
        expected_marking=Marking.PLUS,
        red_ring_required=True,
        marking_roi_shape="rectangle",
    )
    recipe = Recipe.new(
        name="ML LEGACY RECT",
        part_number="ML-RECT",
        description="",
        created_by="test",
        battery_roi=NormalizedRect(0.1, 0.1, 0.8, 0.8),
        terminals=[terminal],
    )
    recipe.classifier_settings = MarkingClassifierSettings(
        method="onnx_ml",
        ml_model_id=str(info["model_id"]),
        ml_model_version=str(info["model_version"]),
        ml_model_sha256=str(info["model_sha256"]),
        ml_minimum_confidence=0.80,
        ml_minimum_margin=0.10,
        ml_test_time_quadrants=False,
    )
    crop = np.full((137, 211, 3), 140, dtype=np.uint8)
    cv2.line(crop, (50, 68), (160, 68), (30, 30, 30), 8)
    outcome = classifier.classify(
        crop, terminal, recipe, np.zeros((10, 10, 3), np.uint8)
    )
    assert outcome.marking == Marking.PLUS
    assert outcome.metrics["ml_terminal_top_method"] == "LEGACY_RECT_DIRECT"
    assert outcome.metrics["ml_input_width_px"] == 211
    assert outcome.metrics["ml_input_height_px"] == 137
    assert outcome.metrics["ml_tta_count"] == 1
    assert outcome.diagnostic_images["terminal_top"].shape[:2] == (137, 211)
