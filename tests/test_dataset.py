from __future__ import annotations

import json
import shutil
from pathlib import Path

from battery_inspector.dataset import export_marking_dataset


FIXTURE = Path(__file__).parent / "fixtures" / "cycle_000006"


def test_export_marking_dataset_uses_only_passing_validation_evidence(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    cycle = runtime / "validation" / "20260819" / "VALIDATE-DATASET"
    cycle.mkdir(parents=True)

    negative_crop = cycle / "negative_marking.png"
    positive_crop = cycle / "positive_marking.png"
    shutil.copy2(FIXTURE / "negative_current.png", negative_crop)
    shutil.copy2(FIXTURE / "positive_current.png", positive_crop)

    terminals = [
        {
            "terminal_key": "negative",
            "role": "negative",
            "expected_marking": "minus",
            "detected_marking": "minus",
            "marking_confidence": 0.98,
            "marking_evaluated": True,
            "marking_pass": True,
            "marking_crop_path": str(negative_crop),
            "diagnostic_image_paths": {"terminal_top": str(negative_crop)},
            "classification_status": "HYBRID_CLASS_ACCEPTED",
        },
        {
            "terminal_key": "positive",
            "role": "positive",
            "expected_marking": "plus",
            "detected_marking": "plus",
            "marking_confidence": 0.99,
            "marking_evaluated": True,
            "marking_pass": True,
            "marking_crop_path": str(positive_crop),
            "diagnostic_image_paths": {"terminal_top": str(positive_crop)},
            "classification_status": "HYBRID_CLASS_ACCEPTED",
        },
    ]
    payload = {
        "validation_mode": True,
        "recipe": {
            "recipe_id": "recipe-dataset",
            "name": "DATASET REGRESSION",
            "revision": 3,
        },
        "result": {
            "inspection_id": "inspection-dataset",
            "cycle_id": "VALIDATE-DATASET",
            "captured_at_utc": "2026-08-19T14:09:53+00:00",
            "disposition": "pass",
            "terminals": terminals,
        },
    }
    (cycle / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    output = tmp_path / "dataset"
    summary = export_marking_dataset(runtime, output)

    assert summary["record_count"] == 2
    assert summary["counts"]["plus"] == 1
    assert summary["counts"]["minus"] == 1
    assert summary["counts"]["blank"] == 0
    assert len(list((output / "plus").glob("*.png"))) == 1
    assert len(list((output / "minus").glob("*.png"))) == 1
    lines = (output / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    labels = {json.loads(line)["label"] for line in lines}
    assert labels == {"plus", "minus"}


def test_export_marking_dataset_deduplicates_identical_bytes_per_class(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    cycle = runtime / "validation" / "20260819" / "CYCLE-1"
    cycle.mkdir(parents=True)
    crop = cycle / "mark.png"
    crop.write_bytes(b"same-image-bytes")
    terminal = {
        "terminal_key": "positive",
        "role": "positive",
        "expected_marking": "plus",
        "detected_marking": "plus",
        "marking_confidence": 0.99,
        "marking_evaluated": True,
        "marking_pass": True,
        "marking_crop_path": str(crop),
        "diagnostic_image_paths": {"terminal_top": str(crop)},
        "classification_status": "CLASS_ACCEPTED",
    }
    payload = {
        "validation_mode": True,
        "recipe": {"recipe_id": "recipe-1", "name": "TEST", "revision": 1},
        "result": {
            "inspection_id": "inspection-1",
            "cycle_id": "cycle-1",
            "captured_at_utc": "2026-08-19T00:00:00+00:00",
            "disposition": "pass",
            "terminals": [terminal, dict(terminal, terminal_key="positive-copy")],
        },
    }
    (cycle / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    summary = export_marking_dataset(runtime, tmp_path / "dataset")

    assert summary["record_count"] == 1
    assert summary["counts"]["plus"] == 1


def test_export_prefers_isolated_terminal_top_for_ml_training(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    cycle = runtime / "validation" / "20260819" / "CYCLE-TOP"
    cycle.mkdir(parents=True)
    raw = cycle / "positive_marking.png"
    top = cycle / "positive_terminal_top.png"
    raw.write_bytes(b"raw-with-ring-context")
    top.write_bytes(b"isolated-terminal-top")
    payload = {
        "validation_mode": True,
        "recipe": {"recipe_id": "r", "name": "R", "revision": 1},
        "result": {
            "inspection_id": "i",
            "cycle_id": "c",
            "captured_at_utc": "2026-08-19T00:00:00+00:00",
            "disposition": "pass",
            "terminals": [
                {
                    "terminal_key": "positive",
                    "role": "positive",
                    "expected_marking": "plus",
                    "detected_marking": "plus",
                    "marking_confidence": 0.99,
                    "marking_evaluated": True,
                    "marking_pass": True,
                    "marking_crop_path": str(raw),
                    "diagnostic_image_paths": {"terminal_top": str(top)},
                    "classification_status": "CLASS_ACCEPTED",
                }
            ],
        },
    }
    (cycle / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "dataset"
    export_marking_dataset(runtime, output)
    exported = next((output / "plus").iterdir())
    assert exported.read_bytes() == b"isolated-terminal-top"


def test_prepare_classification_dataset_keeps_cycle_group_together(tmp_path: Path) -> None:
    from battery_inspector.dataset import prepare_classification_dataset

    exported = tmp_path / "exported"
    (exported / "plus").mkdir(parents=True)
    (exported / "minus").mkdir(parents=True)
    plus = exported / "plus" / "plus.png"
    minus = exported / "minus" / "minus.png"
    plus.write_bytes(b"plus")
    minus.write_bytes(b"minus")
    records = [
        {
            "label": "plus",
            "destination_path": str(plus),
            "cycle_id": "same-cycle",
            "inspection_id": "inspection-1",
        },
        {
            "label": "minus",
            "destination_path": str(minus),
            "cycle_id": "same-cycle",
            "inspection_id": "inspection-1",
        },
    ]
    (exported / "manifest.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )
    output = tmp_path / "prepared"
    summary = prepare_classification_dataset(exported, output, clean=True)
    splits_with_plus = [
        split for split in ("train", "val", "test") if summary["counts"][split]["plus"]
    ]
    splits_with_minus = [
        split for split in ("train", "val", "test") if summary["counts"][split]["minus"]
    ]
    assert splits_with_plus == splits_with_minus


def test_export_skips_legacy_marking_crop_by_default(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    cycle = runtime / "validation" / "20260820" / "LEGACY-CYCLE"
    cycle.mkdir(parents=True)
    crop = cycle / "legacy_marking.png"
    crop.write_bytes(b"unsafe-context-crop")
    terminal = {
        "terminal_key": "positive",
        "role": "positive",
        "expected_marking": "plus",
        "detected_marking": "plus",
        "marking_confidence": 0.99,
        "marking_evaluated": True,
        "marking_pass": True,
        "marking_crop_path": str(crop),
        "classification_status": "LEGACY",
    }
    payload = {
        "validation_mode": True,
        "recipe": {"recipe_id": "legacy", "name": "LEGACY", "revision": 1},
        "result": {
            "inspection_id": "legacy-i",
            "cycle_id": "legacy-c",
            "disposition": "pass",
            "terminals": [terminal],
        },
    }
    (cycle / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    safe = export_marking_dataset(runtime, tmp_path / "safe")
    assert safe["record_count"] == 0

    explicit = export_marking_dataset(
        runtime,
        tmp_path / "legacy-allowed",
        allow_legacy_marking_crops=True,
    )
    assert explicit["record_count"] == 1
