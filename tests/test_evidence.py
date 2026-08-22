from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic_ns

import cv2
import numpy as np
import pytest

from battery_inspector.evidence import (
    EvidenceError,
    FailureRetentionPolicy,
    apply_failure_retention,
    export_evidence_package,
    persist_recipe_reference,
    persist_recipe_validation_records,
    sha256_file,
    stage_reference_capture,
)
from battery_inspector.services.camera import CameraFrame


def _fresh_frame() -> CameraFrame:
    requested = monotonic_ns()
    captured = requested + 1
    return CameraFrame(
        image=np.full((120, 200, 3), 90, dtype=np.uint8),
        sequence=7,
        frame_id="FRAME-0007",
        requested_at_utc="2026-08-19T12:00:00+00:00",
        captured_at_utc="2026-08-19T12:00:00.010000+00:00",
        request_monotonic_ns=requested,
        captured_monotonic_ns=captured,
        camera_frame_id="BASLER-77",
        camera_timestamp_raw=123456789,
        backend_name="test-camera",
    )


def _retained_cycle(
    root: Path,
    *,
    day: str,
    cycle_id: str,
    disposition: str,
    timestamp: datetime,
    payload_bytes: int = 32,
) -> Path:
    directory = root / day / cycle_id
    directory.mkdir(parents=True)
    (directory / "payload.bin").write_bytes(b"x" * payload_bytes)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "result": {
                    "disposition": disposition,
                    "timestamp_utc": timestamp.isoformat(),
                }
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_failure_retention_removes_pass_and_expired_failures_only(tmp_path: Path) -> None:
    now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
    inspections = tmp_path / "runtime" / "inspections"
    passing = _retained_cycle(
        inspections,
        day="20260820",
        cycle_id="PASS-1",
        disposition="pass",
        timestamp=now,
    )
    expired = _retained_cycle(
        inspections,
        day="20260601",
        cycle_id="FAIL-OLD",
        disposition="reject",
        timestamp=now - timedelta(days=80),
    )
    recent = _retained_cycle(
        inspections,
        day="20260819",
        cycle_id="FAIL-NEW",
        disposition="system_fault",
        timestamp=now - timedelta(days=1),
    )
    validation = tmp_path / "runtime" / "validation" / "20260820" / "VALIDATION-PASS"
    validation.mkdir(parents=True)
    (validation / "manifest.json").write_text("{}", encoding="utf-8")

    report = apply_failure_retention(
        inspections,
        FailureRetentionPolicy(max_age_days=30, max_bytes=0),
        now=now,
    )

    assert not passing.exists()
    assert not expired.exists()
    assert recent.is_dir()
    assert validation.is_dir()
    assert report.pass_cycles_removed == 1
    assert report.expired_cycles_removed == 1
    assert report.failure_cycles_remaining == 1


def test_failure_capacity_keeps_newest_cycle_when_one_package_exceeds_limit(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
    inspections = tmp_path / "inspections"
    directories = [
        _retained_cycle(
            inspections,
            day="20260820",
            cycle_id=f"FAIL-{index}",
            disposition="reject",
            timestamp=now + timedelta(minutes=index),
            payload_bytes=128,
        )
        for index in range(3)
    ]

    report = apply_failure_retention(
        inspections,
        FailureRetentionPolicy(max_age_days=0, max_bytes=1),
        now=now,
    )

    assert not directories[0].exists()
    assert not directories[1].exists()
    assert directories[2].is_dir()
    assert report.capacity_cycles_removed == 2
    assert report.failure_cycles_remaining == 1


def test_stage_reference_capture_saves_frame_and_metadata(tmp_path: Path) -> None:
    frame = _fresh_frame()
    reference = stage_reference_capture(
        frame,
        tmp_path / "staging",
        camera_profile={"exposure_us": 9997.0},
    )

    path = Path(reference.path)
    assert path.is_file()
    assert reference.sha256 == sha256_file(path)
    assert reference.width_px == 200
    assert reference.height_px == 120
    assert reference.channels == 3
    assert reference.frame_sequence == 7
    assert reference.frame_id == "FRAME-0007"
    assert reference.camera_frame_id == "BASLER-77"
    assert reference.camera_backend == "test-camera"
    assert reference.camera_profile["exposure_us"] == 9997.0
    assert reference.quality["status"] in {"GOOD", "WARNING", "POOR"}


def test_persist_recipe_reference_creates_immutable_revision_copy(tmp_path: Path) -> None:
    staged = stage_reference_capture(_fresh_frame(), tmp_path / "staging")

    persisted = persist_recipe_reference(
        staged,
        tmp_path / "data",
        recipe_id="recipe-abc",
        revision=3,
    )

    expected = tmp_path / "data" / "recipes" / "recipe-abc" / "revision_0003" / "reference.png"
    assert Path(persisted.path) == expected
    assert expected.is_file()
    assert persisted.sha256 == staged.sha256
    assert persisted.capture_id == staged.capture_id
    assert Path(staged.path).is_file()


def test_reference_capture_writes_review_metadata(tmp_path: Path) -> None:
    reference = stage_reference_capture(_fresh_frame(), tmp_path / "staging")

    metadata_path = Path(reference.path).with_suffix(".json")
    assert metadata_path.is_file()
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["reference"]["capture_id"] == reference.capture_id
    assert payload["camera_frame"]["frame_id"] == "FRAME-0007"


def test_persisted_reference_writes_revision_metadata(tmp_path: Path) -> None:
    staged = stage_reference_capture(_fresh_frame(), tmp_path / "staging")
    persisted = persist_recipe_reference(
        staged,
        tmp_path / "data",
        recipe_id="recipe-meta",
        revision=4,
    )

    metadata_path = Path(persisted.path).with_name("reference.json")
    assert metadata_path.is_file()
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["recipe_id"] == "recipe-meta"
    assert payload["revision"] == 4
    assert payload["reference"]["sha256"] == persisted.sha256


def test_changed_reference_is_rejected_instead_of_silently_relabelled(tmp_path: Path) -> None:
    staged = stage_reference_capture(_fresh_frame(), tmp_path / "staging")
    changed = np.full((120, 200, 3), 210, dtype=np.uint8)
    assert cv2.imwrite(staged.path, changed)

    with pytest.raises(EvidenceError, match="changed after it was captured"):
        persist_recipe_reference(
            staged,
            tmp_path / "data",
            recipe_id="recipe-tampered",
            revision=1,
        )


def test_passing_validation_crops_are_persisted_with_recipe_revision(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "runtime" / "validation" / "cycle"
    source_dir.mkdir(parents=True)
    terminal_path = source_dir / "positive_terminal.png"
    marking_path = source_dir / "positive_marking.png"
    image = np.full((80, 80, 3), 140, dtype=np.uint8)
    assert cv2.imwrite(str(terminal_path), image)
    assert cv2.imwrite(str(marking_path), image[20:60, 20:60])
    records = [
        {
            "disposition": "pass",
            "configuration_hash": "cfg-1",
            "evidence_directory": str(source_dir),
            "terminals": [
                {
                    "terminal_key": "positive",
                    "terminal_crop_path": str(terminal_path),
                    "marking_crop_path": str(marking_path),
                }
            ],
        }
    ]

    persisted = persist_recipe_validation_records(
        records,
        tmp_path / "data",
        recipe_id="recipe-validation",
        revision=2,
        configuration_hash="cfg-1",
    )

    terminal = persisted[0]["terminals"][0]
    assert Path(terminal["terminal_crop_path"]).is_file()
    assert Path(terminal["marking_crop_path"]).is_file()
    assert "revision_0002" in terminal["marking_crop_path"]
    metadata = (
        tmp_path
        / "data"
        / "recipes"
        / "recipe-validation"
        / "revision_0002"
        / "validation.json"
    )
    assert metadata.is_file()


def test_export_evidence_package_contains_complete_cycle(tmp_path: Path) -> None:
    source = tmp_path / "inspections" / "CYCLE-001"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text("{}", encoding="utf-8")
    (source / "positive_marking.png").write_bytes(b"image-bytes")
    nested = source / "diagnostics"
    nested.mkdir()
    (nested / "stamp.json").write_text("{\"angle\": 12}", encoding="utf-8")

    destination = export_evidence_package(source, tmp_path / "exports" / "cycle")

    assert destination.name == "cycle.zip"
    assert destination.is_file()
    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
    assert "CYCLE-001/manifest.json" in names
    assert "CYCLE-001/positive_marking.png" in names
    assert "CYCLE-001/diagnostics/stamp.json" in names


def test_export_evidence_package_rejects_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError, match="was not found"):
        export_evidence_package(tmp_path / "missing", tmp_path / "missing.zip")


def test_export_evidence_package_preserves_cycle_folder(tmp_path: Path) -> None:
    cycle = tmp_path / "runtime" / "inspections" / "20260819" / "CYCLE-42"
    cycle.mkdir(parents=True)
    (cycle / "manifest.json").write_text('{"result":"pass"}', encoding="utf-8")
    image = np.full((16, 24, 3), 90, dtype=np.uint8)
    assert cv2.imwrite(str(cycle / "positive_marking.png"), image)

    exported = export_evidence_package(cycle, tmp_path / "exports" / "cycle-42")

    assert exported.suffix == ".zip"
    assert exported.is_file()
    import zipfile

    with zipfile.ZipFile(exported) as archive:
        names = set(archive.namelist())
    assert "CYCLE-42/manifest.json" in names
    assert "CYCLE-42/positive_marking.png" in names


def test_export_evidence_package_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError, match="was not found"):
        export_evidence_package(tmp_path / "missing", tmp_path / "missing.zip")
