from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from battery_inspector.config import AppConfig, MlConfig
from battery_inspector.station_transfer import (
    BACKUP_MANIFEST_NAME,
    PENDING_RESTORE_NAME,
    StationTransferError,
    apply_pending_restore,
    create_station_backup,
    inspect_station_backup,
    stage_station_restore,
)


def _create_database(path: Path, old_data_root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    reference = old_data_root / "recipes" / "recipe-1" / "revision_0001" / "reference.png"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE recipes (
                recipe_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE audit_events (
                event_id TEXT PRIMARY KEY,
                details_json TEXT NOT NULL
            );
            CREATE TABLE inspections (
                inspection_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO recipes VALUES (?, ?, ?)",
            (
                "recipe-1",
                1,
                json.dumps({"reference_image": {"path": str(reference)}}),
            ),
        )
        connection.execute(
            "INSERT INTO audit_events VALUES (?, ?)",
            ("event-1", json.dumps({"path": str(old_data_root / "events" / "detail.json")})),
        )
        connection.execute(
            "INSERT INTO inspections VALUES (?, ?)",
            ("fail-1", json.dumps({"evidence": str(old_data_root / "inspections" / "fail-1")})),
        )


def _source_station(root: Path) -> tuple[Path, Path]:
    data = root / "runtime"
    model = data / "models" / "qualified" / "polarity_classifier.onnx"
    manifest = model.with_suffix(".json")
    reference = data / "recipes" / "recipe-1" / "revision_0001" / "reference.png"
    model.parent.mkdir(parents=True, exist_ok=True)
    reference.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"onnx-model")
    manifest.write_text('{"model_id":"test"}', encoding="utf-8")
    reference.write_bytes(b"png-data")
    _create_database(data / "battery_inspector.db", data)
    samples = data / "ml_training" / "samples.jsonl"
    samples.parent.mkdir(parents=True, exist_ok=True)
    samples.write_text(
        json.dumps({"image_path": str(data / "ml_training" / "samples" / "plus.png")}) + "\n",
        encoding="utf-8",
    )
    config = AppConfig(
        camera_backend="simulation",
        plc_backend="simulation",
        ml=MlConfig(model_path=str(model), manifest_path=str(manifest)),
    )
    config_path = root / "config.json"
    config.save(config_path)
    return config_path, data


def test_backup_restore_rebases_runtime_paths_and_creates_rollback(tmp_path: Path) -> None:
    source_root = tmp_path / "old_pc" / "PolePosition"
    source_root.mkdir(parents=True)
    config_path, data = _source_station(source_root)
    backup = tmp_path / "station.zip"

    created = create_station_backup(source_root, config_path, data, backup)
    inspected = inspect_station_backup(backup)

    assert created["file_count"] >= 6
    assert inspected["application_version"]
    assert inspected["file_count"] == created["file_count"]

    target_root = tmp_path / "new_pc" / "PolePosition"
    target_root.mkdir(parents=True)
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(
        target_root / "config.json"
    )
    _create_database(target_root / "runtime" / "battery_inspector.db", target_root / "runtime")

    staged = stage_station_restore(target_root, backup)
    assert staged["restart_required"] is True
    assert (target_root / PENDING_RESTORE_NAME).is_file()

    result = apply_pending_restore(target_root, target_root / "config.json")

    target_data = target_root / "runtime"
    restored = AppConfig.load(target_root / "config.json")
    assert restored.data_directory == ""
    assert restored.ml.model_path == str(
        target_data / "models" / "qualified" / "polarity_classifier.onnx"
    )
    assert Path(restored.ml.model_path).read_bytes() == b"onnx-model"
    assert Path(result["rollback_backup"]).is_file()
    assert not (target_root / PENDING_RESTORE_NAME).exists()

    with sqlite3.connect(target_data / "battery_inspector.db") as connection:
        recipe = json.loads(connection.execute("SELECT payload_json FROM recipes").fetchone()[0])
    assert recipe["reference_image"]["path"].startswith(str(target_data))
    sample = json.loads(
        (target_data / "ml_training" / "samples.jsonl").read_text(encoding="utf-8").strip()
    )
    assert sample["image_path"].startswith(str(target_data))


def test_external_configured_model_is_embedded_portably(tmp_path: Path) -> None:
    source_root = tmp_path / "station"
    source_root.mkdir()
    config_path, data = _source_station(source_root)
    external = tmp_path / "engineering_models"
    external.mkdir()
    model = external / "model.onnx"
    manifest = external / "model.json"
    model.write_bytes(b"external-model")
    manifest.write_text("{}", encoding="utf-8")
    AppConfig(ml=MlConfig(model_path=str(model), manifest_path=str(manifest))).save(config_path)
    backup = tmp_path / "portable.zip"

    create_station_backup(source_root, config_path, data, backup)

    with zipfile.ZipFile(backup) as archive:
        names = set(archive.namelist())
    assert "portable_ml/model_path/model.onnx" in names
    assert "portable_ml/manifest_path/model.json" in names


def test_sqlite_sidecars_are_not_copied_beside_consistent_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "station"
    root.mkdir()
    config_path, data = _source_station(root)
    (data / "battery_inspector.db-wal").write_bytes(b"stale-wal")
    (data / "battery_inspector.db-shm").write_bytes(b"stale-shm")
    backup = tmp_path / "backup.zip"

    create_station_backup(root, config_path, data, backup)

    with zipfile.ZipFile(backup) as archive:
        names = set(archive.namelist())
    assert "runtime/battery_inspector.db" in names
    assert "runtime/battery_inspector.db-wal" not in names
    assert "runtime/battery_inspector.db-shm" not in names


def test_import_rejects_path_traversal(tmp_path: Path) -> None:
    malicious = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../outside.txt", "unsafe")
        archive.writestr(BACKUP_MANIFEST_NAME, "{}")

    with pytest.raises(StationTransferError, match="Unsafe backup archive path"):
        inspect_station_backup(malicious)


def test_import_rejects_tampered_member(tmp_path: Path) -> None:
    root = tmp_path / "station"
    root.mkdir()
    config_path, data = _source_station(root)
    backup = tmp_path / "backup.zip"
    create_station_backup(root, config_path, data, backup)
    tampered = tmp_path / "tampered.zip"

    with zipfile.ZipFile(backup) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "config.json":
                payload += b" "
            target.writestr(info.filename, payload)

    with pytest.raises(StationTransferError, match="size check failed|SHA-256 check failed"):
        inspect_station_backup(tampered)
