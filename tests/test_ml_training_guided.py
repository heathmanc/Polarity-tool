from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from battery_inspector.ml_training import MlTrainingStore, TRAINING_LABELS
from battery_inspector.models import NormalizedRect, ReferenceCapture


def _capture(tmp_path: Path, capture_id: str, *, offset: int = 0) -> ReferenceCapture:
    image = np.zeros((300, 500, 3), dtype=np.uint8)
    image[:] = (110 + offset, 120 + offset, 130 + offset)
    cv2.circle(image, (250, 150), 65, (190, 190, 190), -1)
    cv2.line(image, (215, 150), (285, 150), (20, 20, 20), 8)
    path = tmp_path / f"{capture_id}.png"
    assert cv2.imwrite(str(path), image)
    return ReferenceCapture(
        capture_id=capture_id,
        path=str(path),
        sha256="",
        captured_at_utc="2026-08-20T12:00:00+00:00",
        width_px=500,
        height_px=300,
        frame_id=capture_id,
        camera_backend="basler",
        camera_description="unit camera",
        quality={"status": "GOOD"},
    )


def test_guided_store_saves_operator_roi_directly_without_evidence_folder(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "runtime" / "ml_training")
    capture = _capture(tmp_path, "frame-1")
    roi = NormalizedRect(0.36, 0.27, 0.28, 0.46)
    sample = store.save_sample(capture, roi, "minus")

    path = Path(sample.image_path)
    assert path.is_file()
    assert "ml_training" in str(path)
    assert "evidence" not in str(path).lower()
    crop = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert crop is not None
    assert crop.shape[0] >= 48
    assert crop.shape[1] >= 48
    assert store.counts()["minus"] == 1
    record = json.loads(store.manifest_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["source_capture_id"] == "frame-1"
    assert record["roi_shape"] == "circle"
    assert record["crop_contract"] == "taught_circle_masked_square_v1"
    assert crop.shape[0] == crop.shape[1]


def test_guided_store_deduplicates_identical_crop_within_class(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    capture = _capture(tmp_path, "frame-1")
    roi = NormalizedRect(0.36, 0.27, 0.28, 0.46)
    first = store.save_sample(capture, roi, "minus")
    second = store.save_sample(capture, roi, "minus")
    assert first.sample_id == second.sample_id
    assert store.counts()["minus"] == 1


def test_guided_dataset_keeps_same_camera_capture_in_one_split(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    roi_a = NormalizedRect(0.34, 0.25, 0.25, 0.45)
    roi_b = NormalizedRect(0.40, 0.25, 0.25, 0.45)

    # Build enough unique capture groups for every class. PLUS and MINUS from the
    # same frame must always receive the same split.
    shared_capture_ids = []
    for index in range(8):
        capture = _capture(tmp_path, f"shared-{index}", offset=index)
        shared_capture_ids.append(capture.capture_id)
        store.save_sample(capture, roi_a, "plus")
        store.save_sample(capture, roi_b, "minus")
    for label in ("blank", "invalid_marking"):
        for index in range(8):
            capture = _capture(tmp_path, f"{label}-{index}", offset=20 + index)
            store.save_sample(capture, roi_a, label)

    summary = store.prepare_dataset(validation_fraction=0.15, test_fraction=0.15)
    assert set(summary["counts"]) == {"train", "val", "test"}
    for split in ("train", "val", "test"):
        for label in TRAINING_LABELS:
            assert summary["counts"][split][label] >= 1

    assignments = [
        json.loads(line)
        for line in (store.datasets_root / "current" / "dataset_manifest.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    for capture_id in shared_capture_ids:
        splits = {
            item["split"]
            for item in assignments
            if item["capture_group"] == capture_id
        }
        assert len(splits) == 1


def test_guided_dataset_readiness_treats_collection_targets_as_advisory(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    initial = store.dataset_readiness()
    assert initial["collection_targets_are_advisory"] is True
    assert set(initial["classes_without_samples"]) == set(TRAINING_LABELS)
    assert set(initial["classes_below_target"]) == set(TRAINING_LABELS)
    assert initial["recommended"] == {
        "plus": 100,
        "minus": 100,
        "blank": 100,
        "invalid_marking": 100,
    }
    # Compatibility keys no longer gate the guided workflow.
    assert initial["minimum_met"] is True
    assert initial["classes_below_minimum"] == []


def test_guided_dataset_can_prepare_below_advisory_targets(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    roi = NormalizedRect(0.34, 0.25, 0.25, 0.45)
    for label_index, label in enumerate(TRAINING_LABELS):
        for capture_index in range(2):
            capture = _capture(
                tmp_path,
                f"{label}-{capture_index}",
                offset=label_index * 8 + capture_index,
            )
            store.save_sample(capture, roi, label)

    readiness = store.dataset_readiness()
    assert readiness["production_target_met"] is False
    summary = store.prepare_dataset(validation_fraction=0.15, test_fraction=0.15)
    assert summary["record_count"] == 8
    assert summary["training_ready"] is True
    assert summary["collection_targets_are_advisory"] is True
    for label in TRAINING_LABELS:
        assert summary["counts"]["train"][label] >= 1


def test_guided_store_saves_multiple_rois_from_one_capture_as_one_batch(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    capture = _capture(tmp_path, "frame-batch", offset=3)
    left = NormalizedRect(0.24, 0.28, 0.24, 0.42)
    right = NormalizedRect(0.52, 0.28, 0.24, 0.42)

    results = store.save_samples(
        capture,
        [
            ("ml_top_1", left, "plus"),
            ("ml_top_2", right, "minus"),
        ],
        collection_tag="Group31 / supplier A",
    )

    assert len(results) == 2
    records = [record for record, duplicate in results if not duplicate]
    assert len(records) == 2
    assert {item.label for item in records} == {"plus", "minus"}
    assert {item.source_capture_id for item in records} == {"frame-batch"}
    assert {item.collection_tag for item in records} == {"Group31 / supplier A"}
    assert {item.roi_key for item in records} == {"ml_top_1", "ml_top_2"}
    assert {item.batch_index for item in records} == {1, 2}

    readiness = store.dataset_readiness()
    assert readiness["total_samples"] == 2
    assert readiness["total_capture_groups"] == 1
    assert readiness["collection_tag_count"] == 1
    assert readiness["collection_tags"] == {"Group31 / supplier A": 2}


def test_guided_store_rejects_bad_roi_before_writing_any_batch_samples(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    capture = _capture(tmp_path, "frame-bad")
    valid = NormalizedRect(0.30, 0.25, 0.25, 0.45)
    too_small = NormalizedRect(0.50, 0.50, 0.005, 0.005)

    import pytest

    with pytest.raises(Exception):
        store.save_samples(
            capture,
            [("good", valid, "plus"), ("bad", too_small, "minus")],
        )

    assert store.records() == []


def test_v011_training_sample_manifest_without_batch_metadata_remains_readable(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    capture = _capture(tmp_path, "legacy-frame")
    sample = store.save_sample(capture, NormalizedRect(0.36, 0.27, 0.28, 0.46), "minus")
    payload = sample.to_dict()
    payload.pop("collection_tag", None)
    payload.pop("batch_index", None)
    payload.pop("roi_key", None)
    store.manifest_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    loaded = store.records()
    assert len(loaded) == 1
    assert loaded[0].collection_tag == ""
    assert loaded[0].batch_index == 0
    assert loaded[0].roi_key == ""


def test_pre_v017_unreadable_sample_is_ignored_by_clean_store(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    legacy_path = store.samples_root / "unreadable" / "legacy.png"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    image = np.full((96, 96, 3), 127, dtype=np.uint8)
    assert cv2.imwrite(str(legacy_path), image)
    import hashlib

    digest = hashlib.sha256(legacy_path.read_bytes()).hexdigest()
    payload = {
        "sample_id": "legacy-unreadable",
        "label": "unreadable",
        "image_path": str(legacy_path),
        "source_image_path": str(legacy_path),
        "source_capture_id": "legacy-capture",
        "captured_at_utc": "2026-08-19T00:00:00+00:00",
        "roi": NormalizedRect(0.2, 0.2, 0.5, 0.5).to_dict(),
        "sha256": digest,
        "width_px": 96,
        "height_px": 96,
        "roi_shape": "rectangle",
        "crop_contract": "legacy_rect_v1",
    }
    store.manifest_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert store.sample_catalog() == []
    readiness = store.dataset_readiness()
    assert readiness["total_samples"] == 0
    assert readiness["total_stored_samples"] == 0


def test_guided_store_can_relabel_and_move_persistent_sample(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    capture = _capture(tmp_path, "relabel-frame")
    sample = store.save_sample(capture, NormalizedRect(0.36, 0.27, 0.28, 0.46), "minus")
    old_path = Path(sample.image_path)
    assert old_path.is_file()

    result = store.relabel_sample(sample.sample_id, "plus")
    assert result["merged_duplicate"] is False
    records = store.records()
    assert len(records) == 1
    assert records[0].sample_id == sample.sample_id
    assert records[0].label == "plus"
    assert records[0].source_capture_id == "relabel-frame"
    assert Path(records[0].image_path).is_file()
    assert Path(records[0].image_path).parent.name == "plus"
    assert not old_path.exists()


def test_latest_training_result_recovers_exported_candidate_without_evaluation(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    run = store.runs_root / "RUN-001"
    run.mkdir(parents=True)
    model = run / "polarity_classifier.onnx"
    model.write_bytes(b"candidate")
    import hashlib

    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    (run / "polarity_classifier.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": "unit-candidate",
                "model_version": "1",
                "classes": list(TRAINING_LABELS),
                "input_size": [224, 224],
                "model_sha256": digest,
                "metadata": {
                    "training_parameters": {"epochs": 1},
                    "counts": {"train": {label: 1 for label in TRAINING_LABELS}},
                },
            }
        ),
        encoding="utf-8",
    )

    recovered = store.latest_training_result()
    assert recovered is not None
    assert recovered["run_id"] == "RUN-001"
    assert recovered["model_sha256"] == digest
    assert recovered["evaluation_recovered"] is False
    assert recovered["evaluation"]["held_out_available"] is False


def test_guided_circle_capture_saves_masked_square_contract(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    capture = _capture(tmp_path, "circle-frame", offset=4)
    result = store.save_samples(
        capture,
        [("positive", NormalizedRect(0.36, 0.27, 0.28, 0.46), "plus")],
        roi_shape="circle",
    )[0][0]

    crop = cv2.imread(result.image_path, cv2.IMREAD_COLOR)
    assert crop is not None
    assert crop.shape[0] == crop.shape[1]
    assert result.roi_shape == "circle"
    assert result.crop_contract == "taught_circle_masked_square_v1"
    assert result.crop_quality["roi_shape"] == "circle"


def test_prepare_dataset_uses_every_clean_circle_sample(tmp_path: Path) -> None:
    store = MlTrainingStore(tmp_path / "training")
    roi = NormalizedRect(0.34, 0.25, 0.25, 0.45)
    labels = TRAINING_LABELS
    saved = 0
    for index in range(65):
        image = np.full((300, 500, 3), 100 + (index % 40), dtype=np.uint8)
        cv2.circle(image, (250, 150), 65, (175, 175, 175), -1)
        cv2.putText(
            image,
            str(index),
            (225, 165),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (15, 15, 15),
            2,
            cv2.LINE_AA,
        )
        path = tmp_path / f"clean-{index}.png"
        assert cv2.imwrite(str(path), image)
        capture = ReferenceCapture(
            capture_id=f"clean-{index}",
            path=str(path),
            sha256="",
            captured_at_utc="2026-08-20T12:00:00+00:00",
            width_px=500,
            height_px=300,
            frame_id=f"clean-{index}",
            camera_backend="basler",
            camera_description="unit camera",
            quality={"status": "GOOD"},
        )
        before = len(store.records())
        store.save_sample(capture, roi, labels[index % 3])
        after = len(store.records())
        saved += after - before

    assert saved == 65
    summary = store.prepare_dataset(validation_fraction=0.15, test_fraction=0.15)
    assert summary["record_count"] == 65
    assert sum(sum(split.values()) for split in summary["counts"].values()) == 65
