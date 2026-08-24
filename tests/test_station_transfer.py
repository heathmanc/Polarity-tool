from __future__ import annotations

import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from battery_inspector import station_transfer

from conftest import ROOT
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
    with closing(sqlite3.connect(path)) as connection, connection:
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

    with closing(sqlite3.connect(target_data / "battery_inspector.db")) as connection:
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


# --- SQLite handles must not outlive their use ------------------------------
#
# sqlite3's connection context manager commits or rolls back but never closes.
# A leaked handle is invisible on Linux, where an open file can still be
# unlinked, and fatal on Windows, where it cannot: backup failed with
# "WinError 32: The process cannot access the file because it is being used by
# another process" when its temporary directory was cleaned up. Windows is the
# only platform the station ships on, so these tests assert the invariant
# directly rather than relying on the platform to expose it.


def _connection_is_closed(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


@pytest.fixture()
def tracked_connections(monkeypatch):
    """Record every SQLite connection station_transfer opens."""

    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(
        "battery_inspector.station_transfer.sqlite3.connect", tracking_connect
    )
    return opened


def test_backup_leaves_no_open_database_handles(tmp_path: Path, tracked_connections) -> None:
    root = tmp_path / "station"
    root.mkdir()
    config_path, data = _source_station(root)

    create_station_backup(root, config_path, data, tmp_path / "backup.zip")

    assert tracked_connections, "The backup opened no database at all"
    assert all(_connection_is_closed(item) for item in tracked_connections)


def test_backup_of_a_station_without_a_database_leaves_no_open_handles(
    tmp_path: Path, tracked_connections
) -> None:
    """A freshly installed station has not opened its repository yet."""

    root = tmp_path / "station"
    data = root / "runtime"
    data.mkdir(parents=True)
    config_path = root / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(config_path)

    create_station_backup(root, config_path, data, tmp_path / "backup.zip")

    assert tracked_connections
    assert all(_connection_is_closed(item) for item in tracked_connections)


def test_restore_leaves_no_open_database_handles(tmp_path: Path, tracked_connections) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path, data = _source_station(source_root)
    backup = tmp_path / "backup.zip"
    create_station_backup(source_root, config_path, data, backup)

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_config = target_root / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(target_config)
    stage_station_restore(target_root, backup)
    apply_pending_restore(target_root, target_config)

    assert tracked_connections
    assert all(_connection_is_closed(item) for item in tracked_connections)


# --- scratch-space cleanup must never fail the operation --------------------


def test_backup_survives_a_temp_directory_windows_refuses_to_delete(
    tmp_path: Path, monkeypatch
) -> None:
    """A locked scratch file must not fail the backup, or the restore above it.

    Windows refuses to delete a file another process holds open, and an
    antivirus or search indexer scanning a freshly written .db is enough to
    cause that for a moment. Linux cannot reproduce it -- an open file unlinks
    happily -- so this emulates TemporaryDirectory's documented contract:
    cleanup raises unless ignore_cleanup_errors was requested.

    Observed in the field as "Workstation restore not applied ... [WinError 32]"
    because a restore takes a rollback backup before swapping any data.
    """

    import tempfile as tempfile_module

    real_temporary_directory = tempfile_module.TemporaryDirectory

    class HostileTemporaryDirectory(real_temporary_directory):
        def cleanup(self) -> None:
            if not self._ignore_cleanup_errors:
                raise PermissionError(
                    32,
                    "The process cannot access the file because it is being used "
                    "by another process",
                )
            # Requested tolerance: leave the scratch for the OS temp cleaner.

    monkeypatch.setattr(
        station_transfer.tempfile, "TemporaryDirectory", HostileTemporaryDirectory
    )

    root = tmp_path / "station"
    root.mkdir()
    config_path, data = _source_station(root)
    backup = tmp_path / "backup.zip"

    created = create_station_backup(root, config_path, data, backup)

    assert backup.is_file()
    assert created["file_count"] >= 6
    with zipfile.ZipFile(backup) as archive:
        assert "runtime/battery_inspector.db" in set(archive.namelist())
        assert archive.testzip() is None


# --- backup content analysis ------------------------------------------------


def test_backup_analyzer_classifies_every_station_path() -> None:
    """The report must name every category, including ones only old backups have.

    New backups no longer carry prepared datasets or training runs, but the
    report exists to explain an archive that already does -- so classification
    is tested directly rather than through a freshly written backup.
    """

    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from analyze_station_backup import classify
    finally:
        sys.path.pop(0)

    expectations = {
        "config.json": ("Station configuration", True),
        "pole_position_backup.json": ("Backup manifest", True),
        "runtime/battery_inspector.db": ("Recipe database", True),
        "runtime/recipes/r1/revision_0001/reference.png": ("Recipe references", True),
        "runtime/validation/run_1/full.jpg": ("Validation evidence", True),
        "runtime/models/polarity/v1/polarity_classifier.onnx": ("Installed models", True),
        "runtime/ml_training/samples/plus/a.png": ("ML training samples", True),
        "runtime/ml_training/samples.jsonl": ("ML training index", True),
        "runtime/ml_training/datasets/current/train/plus/a.png": ("ML prepared dataset", False),
        "runtime/ml_training/runs/run_0/weights/best.pt": ("ML training runs", False),
        "runtime/inspections/20260101/CYCLE-1/full.jpg": ("Failure evidence", False),
        "runtime/archive_pre_v017_20260101T000000Z/old.jpg": ("Pre-v0.17 archive", False),
    }
    for name, (label, essential) in expectations.items():
        actual_label, actual_essential, _note = classify(name)
        assert actual_label == label, (name, actual_label)
        assert actual_essential is essential, (name, actual_essential)


def test_backup_analyzer_reports_the_split_for_an_existing_backup(tmp_path: Path) -> None:
    """Run the report against an archive shaped like one written before v0.23.5."""

    import io
    import runpy
    import sys
    from contextlib import redirect_stdout

    legacy = tmp_path / "legacy-backup.zip"
    with zipfile.ZipFile(legacy, "w") as archive:
        archive.writestr("config.json", "{}")
        archive.writestr("runtime/battery_inspector.db", b"db")
        archive.writestr("runtime/ml_training/samples/plus/a.png", b"s" * 1000)
        archive.writestr("runtime/ml_training/runs/run_0/weights/best.pt", b"w" * 8000)
        archive.writestr("runtime/inspections/20260101/CYCLE-1/full.jpg", b"e" * 4000)
        archive.writestr(BACKUP_MANIFEST_NAME, json.dumps({"schema_version": 1}))

    argv = sys.argv
    sys.argv = ["analyze_station_backup.py", str(legacy), "--json"]
    try:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            try:
                runpy.run_path(
                    str(ROOT / "scripts" / "analyze_station_backup.py"),
                    run_name="__main__",
                )
            except SystemExit as exit_code:
                assert exit_code.code == 0
        report = json.loads(buffer.getvalue())
    finally:
        sys.argv = argv

    assert report["regenerable_bytes"] == 12000
    assert report["essential_bytes"] > 0
    assert "Other" not in report["categories"]


# --- derived training artifacts stay out of backups -------------------------


def _station_with_training_artifacts(root: Path) -> tuple[Path, Path]:
    config_path, data = _source_station(root)
    written = {
        # Kept: the taught crops cannot be recreated without recapturing them.
        "ml_training/samples/plus/plus_a.png": b"sample",
        "ml_training/samples.jsonl": b'{"image_path": "x"}\n',
        # Dropped: train/val/test copies of the samples above.
        "ml_training/datasets/current/train/plus/plus_a.png": b"copy",
        "ml_training/datasets/current/val/plus/plus_a.png": b"copy",
        # Dropped: checkpoints and plots from past training.
        "ml_training/runs/run_0/training_runs/weights/best.pt": b"weights" * 512,
        "ml_training/runs/run_0/polarity_classifier.onnx": b"candidate",
        # Dropped: full-resolution captures not yet accepted anywhere. On a real
        # station these were the single largest thing in the archive.
        "ml_training/staging/reference-abc.png": b"staged" * 4096,
        "recipe_staging/reference-def.png": b"staged" * 4096,
        # Kept: the installed package a station actually inspects with.
        "models/polarity/v1/polarity_classifier.onnx": b"installed",
    }
    for relative, payload in written.items():
        target = data / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return config_path, data


def test_backup_excludes_prepared_datasets_and_training_runs(tmp_path: Path) -> None:
    root = tmp_path / "station"
    root.mkdir()
    config_path, data = _station_with_training_artifacts(root)
    backup = tmp_path / "backup.zip"

    create_station_backup(root, config_path, data, backup)

    with zipfile.ZipFile(backup) as archive:
        names = set(archive.namelist())

    assert not [name for name in names if "/ml_training/datasets/" in name]
    assert not [name for name in names if "/ml_training/runs/" in name]
    assert not [name for name in names if "/ml_training/staging/" in name]
    assert not [name for name in names if "/recipe_staging/" in name]


def test_backup_still_carries_what_a_replacement_station_needs(tmp_path: Path) -> None:
    root = tmp_path / "station"
    root.mkdir()
    config_path, data = _station_with_training_artifacts(root)
    backup = tmp_path / "backup.zip"

    create_station_backup(root, config_path, data, backup)

    with zipfile.ZipFile(backup) as archive:
        names = set(archive.namelist())

    assert "runtime/ml_training/samples/plus/plus_a.png" in names
    assert "runtime/ml_training/samples.jsonl" in names
    assert "runtime/models/polarity/v1/polarity_classifier.onnx" in names
    assert "runtime/battery_inspector.db" in names
    assert "config.json" in names


def test_backup_manifest_states_what_it_dropped(tmp_path: Path) -> None:
    """A backup has to describe itself honestly; restore trusts the manifest."""

    root = tmp_path / "station"
    root.mkdir()
    config_path, data = _station_with_training_artifacts(root)
    backup = tmp_path / "backup.zip"

    create_station_backup(root, config_path, data, backup)

    with zipfile.ZipFile(backup) as archive:
        manifest = json.loads(archive.read(BACKUP_MANIFEST_NAME).decode("utf-8"))

    assert manifest["contents"]["ml_training_samples"] is True
    assert manifest["contents"]["ml_prepared_datasets"] is False
    assert manifest["contents"]["ml_training_runs"] is False
    assert manifest["contents"]["staged_captures"] is False
    assert sorted(manifest["excluded_data_prefixes"]) == [
        "ml_training/datasets/",
        "ml_training/runs/",
        "ml_training/staging/",
        "recipe_staging/",
    ]
    # The schema is unchanged, so backups written before this still restore.
    assert manifest["schema_version"] == 1


def test_a_restore_of_a_lean_backup_round_trips(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path, data = _station_with_training_artifacts(source_root)
    backup = tmp_path / "backup.zip"
    create_station_backup(source_root, config_path, data, backup)

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_config = target_root / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(target_config)
    stage_station_restore(target_root, backup)
    apply_pending_restore(target_root, target_config)

    restored = target_root / "runtime"
    assert (restored / "ml_training" / "samples" / "plus" / "plus_a.png").is_file()
    assert (restored / "models" / "polarity" / "v1" / "polarity_classifier.onnx").is_file()
    assert not (restored / "ml_training" / "runs").exists()
    assert not (restored / "ml_training" / "datasets").exists()
    assert not (restored / "ml_training" / "staging").exists()
    assert not (restored / "recipe_staging").exists()


# --- a failed restore must not trap the station -----------------------------


def test_a_failed_restore_clears_the_pending_marker(tmp_path: Path, monkeypatch) -> None:
    """Otherwise every restart retries the same failure and no import is allowed.

    stage_station_restore refuses while a marker exists, and the marker used to
    survive a failure, so a station that failed one restore could never import
    another -- and re-attempted the failing restore on every launch, writing a
    full rollback archive each time.
    """

    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path, data = _source_station(source_root)
    backup = tmp_path / "backup.zip"
    create_station_backup(source_root, config_path, data, backup)

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_config = target_root / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(target_config)
    stage_station_restore(target_root, backup)
    assert (target_root / PENDING_RESTORE_NAME).is_file()

    def explode(*args, **kwargs):
        raise StationTransferError("restore failed for the test")

    monkeypatch.setattr(station_transfer, "_rebase_database", explode)

    with pytest.raises(StationTransferError, match="restore failed for the test"):
        apply_pending_restore(target_root, target_config)

    assert not (target_root / PENDING_RESTORE_NAME).exists()


def test_a_failed_restore_allows_importing_again(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path, data = _source_station(source_root)
    backup = tmp_path / "backup.zip"
    create_station_backup(source_root, config_path, data, backup)

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_config = target_root / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(target_config)
    stage_station_restore(target_root, backup)

    monkeypatch.setattr(
        station_transfer,
        "_rebase_database",
        lambda *a, **k: (_ for _ in ()).throw(StationTransferError("boom")),
    )
    with pytest.raises(StationTransferError):
        apply_pending_restore(target_root, target_config)

    # The operator can now stage the same backup again rather than being stuck.
    monkeypatch.undo()
    staged = stage_station_restore(target_root, backup)

    assert staged["restart_required"] is True
    assert apply_pending_restore(target_root, target_config)["status"] == "restored"


def test_a_failed_restore_records_why(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path, data = _source_station(source_root)
    backup = tmp_path / "backup.zip"
    create_station_backup(source_root, config_path, data, backup)

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_config = target_root / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(target_config)
    stage_station_restore(target_root, backup)

    monkeypatch.setattr(
        station_transfer,
        "_rebase_database",
        lambda *a, **k: (_ for _ in ()).throw(StationTransferError("disk went away")),
    )
    with pytest.raises(StationTransferError):
        apply_pending_restore(target_root, target_config)

    recorded = json.loads(
        (target_root / ".pole_position_restore_result.json").read_text(encoding="utf-8")
    )
    assert recorded["status"] == "failed"
    assert "disk went away" in recorded["error"]


def test_a_failed_restore_leaves_the_station_data_untouched(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path, data = _source_station(source_root)
    backup = tmp_path / "backup.zip"
    create_station_backup(source_root, config_path, data, backup)

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_config = target_root / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(target_config)
    target_data = target_root / "runtime"
    target_data.mkdir()
    sentinel = target_data / "existing.txt"
    sentinel.write_text("original station data", encoding="utf-8")
    stage_station_restore(target_root, backup)

    monkeypatch.setattr(
        station_transfer,
        "_rebase_database",
        lambda *a, **k: (_ for _ in ()).throw(StationTransferError("boom")),
    )
    with pytest.raises(StationTransferError):
        apply_pending_restore(target_root, target_config)

    assert sentinel.read_text(encoding="utf-8") == "original station data"


# --- rollback archives are bounded ------------------------------------------


def test_rollback_archives_are_pruned_to_the_retention_count(tmp_path: Path) -> None:
    """Each rollback is a full station copy, so they cannot accumulate forever."""

    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path, data = _source_station(source_root)
    backup = tmp_path / "backup.zip"
    create_station_backup(source_root, config_path, data, backup)

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_config = target_root / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(target_config)

    rollbacks = target_root / "restore_rollback"
    rollbacks.mkdir()
    for index in range(6):
        (rollbacks / f"Pole_Position_PreRestore_2026010{index}_000000_abcdef12.zip").write_bytes(
            b"old rollback"
        )
    # Anything the operator parked here is not ours to remove.
    keepsake = rollbacks / "operator-notes.zip"
    keepsake.write_bytes(b"not ours")

    stage_station_restore(target_root, backup)
    apply_pending_restore(target_root, target_config)

    remaining = sorted(item.name for item in rollbacks.glob("Pole_Position_PreRestore_*.zip"))

    assert len(remaining) == station_transfer.ROLLBACK_RETENTION_COUNT
    # The newest survive, including the one this restore just wrote.
    assert any(name.startswith("Pole_Position_PreRestore_20") for name in remaining)
    assert keepsake.is_file()


def test_a_failed_restore_keeps_its_rollback_archive(tmp_path: Path, monkeypatch) -> None:
    """Pruning happens only after success; a failure may still need to undo."""

    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path, data = _source_station(source_root)
    backup = tmp_path / "backup.zip"
    create_station_backup(source_root, config_path, data, backup)

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_config = target_root / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(target_config)
    stage_station_restore(target_root, backup)

    monkeypatch.setattr(
        station_transfer,
        "_rebase_database",
        lambda *a, **k: (_ for _ in ()).throw(StationTransferError("boom")),
    )
    with pytest.raises(StationTransferError):
        apply_pending_restore(target_root, target_config)

    written = list((target_root / "restore_rollback").glob("Pole_Position_PreRestore_*.zip"))
    assert len(written) == 1


def test_a_rollback_backup_failure_does_not_leave_the_station_retrying(
    tmp_path: Path, monkeypatch
) -> None:
    """The exact field failure: WinError 32 while taking the rollback backup.

    A restore takes a rollback backup before touching any data, so this is the
    first thing that can fail and the one an operator actually saw. It must
    clear the pending flag like any other failure -- otherwise the station
    re-attempts the same restore on every launch and refuses new imports.
    """

    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path, data = _source_station(source_root)
    backup = tmp_path / "backup.zip"
    create_station_backup(source_root, config_path, data, backup)

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_config = target_root / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(target_config)
    stage_station_restore(target_root, backup)

    def refuse(*args, **kwargs):
        raise PermissionError(
            32,
            "The process cannot access the file because it is being used by another process",
        )

    monkeypatch.setattr(station_transfer, "create_station_backup", refuse)

    with pytest.raises(PermissionError):
        apply_pending_restore(target_root, target_config)

    assert not (target_root / PENDING_RESTORE_NAME).exists(), (
        "the station would retry this restore on every launch"
    )

    # And the operator can import again rather than being told one is pending.
    monkeypatch.undo()
    assert stage_station_restore(target_root, backup)["restart_required"] is True


# --- transient Windows file locks must not fail a backup --------------------


def test_a_momentary_lock_on_the_snapshot_is_retried(tmp_path: Path, monkeypatch) -> None:
    """An antivirus scanning the fresh database holds it for a moment.

    That lock is what produces "[WinError 32] The process cannot access the
    file", and it can strike while the snapshot is being read into the archive
    -- a point no amount of cleanup tolerance covers, because the failure is the
    read itself.
    """

    root = tmp_path / "station"
    root.mkdir()
    config_path, data = _source_station(root)
    backup = tmp_path / "backup.zip"

    real_open = Path.open
    attempts = {"count": 0}

    def flaky_open(self, *args, **kwargs):
        if self.name == "battery_inspector.db" and attempts["count"] < 2:
            attempts["count"] += 1
            raise PermissionError(
                32,
                "The process cannot access the file because it is being used by another process",
            )
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)
    monkeypatch.setattr(station_transfer, "SHARING_VIOLATION_BACKOFF_SECONDS", 0.0)

    created = create_station_backup(root, config_path, data, backup)

    assert attempts["count"] == 2, "the lock should have been hit and retried"
    assert backup.is_file()
    with zipfile.ZipFile(backup) as archive:
        assert "runtime/battery_inspector.db" in set(archive.namelist())
        assert archive.testzip() is None
    assert created["file_count"] >= 6


def test_a_lock_that_never_clears_explains_itself(tmp_path: Path, monkeypatch) -> None:
    """The operator needs to know it is antivirus, not corrupt data."""

    root = tmp_path / "station"
    root.mkdir()
    config_path, data = _source_station(root)

    real_open = Path.open

    def always_locked(self, *args, **kwargs):
        if self.name == "battery_inspector.db":
            raise PermissionError(
                32,
                "The process cannot access the file because it is being used by another process",
            )
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", always_locked)
    monkeypatch.setattr(station_transfer, "SHARING_VIOLATION_BACKOFF_SECONDS", 0.0)

    with pytest.raises(StationTransferError) as failure:
        create_station_backup(root, config_path, data, tmp_path / "backup.zip")

    message = str(failure.value)
    assert "antivirus" in message
    assert "WinError 32" in message or "cannot access the file" in message
