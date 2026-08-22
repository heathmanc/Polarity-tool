from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable
from uuid import uuid4

from battery_inspector.build_info import software_build_info
from battery_inspector.config import AppConfig


BACKUP_SCHEMA_VERSION = 1
BACKUP_MANIFEST_NAME = "pole_position_backup.json"
PENDING_RESTORE_NAME = ".pole_position_restore_pending.json"
RESTORE_RESULT_NAME = ".pole_position_restore_result.json"
RESTORE_STAGING_DIRECTORY = ".pole_position_restore_staging"
ROLLBACK_DIRECTORY = "restore_rollback"
MAX_ARCHIVE_FILES = 200_000
MAX_ARCHIVE_BYTES = 25 * 1024**3
MAX_MANIFEST_BYTES = 4 * 1024**2


class StationTransferError(ValueError):
    """Raised when a backup or restore cannot be completed safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _hash_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return _hash_stream(handle)[0]


def _safe_archive_path(name: str) -> str:
    normalized = str(name or "").replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(":" in part for part in path.parts)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise StationTransferError(f"Unsafe backup archive path: {name!r}")
    return path.as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _iter_regular_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if path.is_symlink():
            raise StationTransferError(f"Backup source contains a symbolic link: {path}")
        if path.is_file():
            yield path


def _resolve_project_path(project_root: Path, value: str) -> Path:
    path = Path(str(value or "").strip()).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        # closing(), not `with connection`: sqlite3's connection context manager
        # commits or rolls back but never closes. A leaked handle keeps the file
        # open, and Windows refuses to delete or replace an open file, so the
        # caller's temporary directory cleanup fails with WinError 32.
        with (
            closing(sqlite3.connect(source)) as source_connection,
            closing(sqlite3.connect(destination)) as target,
        ):
            source_connection.backup(target)
            result = target.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise StationTransferError(f"Could not create a consistent database snapshot: {exc}") from exc
    if not result or str(result[0]).lower() != "ok":
        raise StationTransferError("The station database did not pass SQLite quick_check")


def _write_zip_member(
    archive: zipfile.ZipFile,
    archive_name: str,
    source: Path,
) -> dict[str, Any]:
    archive_name = _safe_archive_path(archive_name)
    digest = hashlib.sha256()
    size = 0
    info = zipfile.ZipInfo.from_file(source, arcname=archive_name)
    info.compress_type = zipfile.ZIP_DEFLATED
    with source.open("rb") as input_file, archive.open(info, "w", force_zip64=True) as output:
        while True:
            chunk = input_file.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return {"path": archive_name, "size_bytes": size, "sha256": digest.hexdigest()}


def create_station_backup(
    project_root: Path,
    config_path: Path,
    data_directory: Path,
    destination: Path,
) -> dict[str, Any]:
    """Create one portable, checksummed workstation-migration ZIP."""

    project_root = project_root.resolve()
    config_path = config_path.resolve()
    data_directory = data_directory.resolve()
    destination = destination.expanduser().resolve()
    if not config_path.is_file():
        raise StationTransferError(f"Station configuration was not found: {config_path}")
    if destination.suffix.lower() != ".zip":
        destination = destination.with_suffix(".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_zip = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")

    try:
        config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StationTransferError(f"Station configuration is not valid JSON: {exc}") from exc
    if not isinstance(config_payload, dict):
        raise StationTransferError("Station configuration must contain a JSON object")

    sources: list[tuple[str, Path]] = [("config.json", config_path)]
    portable_ml: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="pole_position_backup_") as temporary_directory:
        temporary_root = Path(temporary_directory)
        database = data_directory / "battery_inspector.db"
        snapshot = temporary_root / "battery_inspector.db"
        if database.is_file():
            _sqlite_snapshot(database, snapshot)
        else:
            # A newly installed destination may not have opened its repository
            # yet. Preserve that valid empty state as an inspectable SQLite file.
            with closing(sqlite3.connect(snapshot)) as connection:
                connection.execute("PRAGMA user_version")
        sources.append(("runtime/battery_inspector.db", snapshot))

        for source in _iter_regular_files(data_directory):
            if source.resolve() in {destination, temporary_zip}:
                continue
            relative = source.relative_to(data_directory).as_posix()
            if source == database:
                continue
            if relative in {
                "battery_inspector.db-wal",
                "battery_inspector.db-shm",
                "battery_inspector.db-journal",
            }:
                # The SQLite backup API produced a self-contained database above.
                # Copying live sidecars beside it could make the restored snapshot
                # appear inconsistent or replay transactions from another file.
                continue
            sources.append((f"runtime/{relative}", source))

        ml_payload = config_payload.get("ml", {})
        if isinstance(ml_payload, dict):
            for key in ("model_path", "manifest_path"):
                original = str(ml_payload.get(key, "") or "").strip()
                if not original:
                    continue
                source = _resolve_project_path(project_root, original)
                if not source.is_file() or _is_relative_to(source, data_directory):
                    continue
                archive_name = f"portable_ml/{key}/{source.name}"
                sources.append((archive_name, source))
                portable_ml[key] = {
                    "archive_path": archive_name,
                    "original_path": original,
                }

        archive_names: set[str] = set()
        records: list[dict[str, Any]] = []
        try:
            with zipfile.ZipFile(
                temporary_zip,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                for archive_name, source in sources:
                    safe_name = _safe_archive_path(archive_name)
                    if safe_name in archive_names:
                        raise StationTransferError(f"Duplicate backup archive path: {safe_name}")
                    archive_names.add(safe_name)
                    records.append(_write_zip_member(archive, safe_name, source))

                manifest = {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "application": "Pole Position",
                    "created_at_utc": _utc_now(),
                    "software": software_build_info(),
                    "source": {
                        "project_root": str(project_root),
                        "data_directory": str(data_directory),
                        "config_path": str(config_path),
                    },
                    "contents": {
                        "settings": True,
                        "recipe_database_and_assets": True,
                        "validation_data": True,
                        "ml_training_and_models": True,
                        "audit_history": True,
                        "retained_failure_evidence": True,
                        "production_pass_history": False,
                    },
                    "portable_ml": portable_ml,
                    "files": records,
                }
                archive.writestr(
                    BACKUP_MANIFEST_NAME,
                    json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
                )
        except Exception:
            temporary_zip.unlink(missing_ok=True)
            raise

    os.replace(temporary_zip, destination)
    return {
        "path": str(destination),
        "file_count": len(records),
        "size_bytes": destination.stat().st_size,
        "sha256": _sha256_file(destination),
        "created_at_utc": manifest["created_at_utc"],
        "schema_version": BACKUP_SCHEMA_VERSION,
    }


def _read_manifest(archive: zipfile.ZipFile) -> tuple[dict[str, Any], dict[str, zipfile.ZipInfo]]:
    entries: dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for info in archive.infolist():
        safe_name = _safe_archive_path(info.filename)
        if safe_name in entries:
            raise StationTransferError(f"Backup contains a duplicate path: {safe_name}")
        if info.flag_bits & 0x1:
            raise StationTransferError("Encrypted backup ZIPs are not supported")
        if info.is_dir():
            continue
        entries[safe_name] = info
        total_size += int(info.file_size)
        if len(entries) > MAX_ARCHIVE_FILES:
            raise StationTransferError("Backup contains too many files")
        if total_size > MAX_ARCHIVE_BYTES:
            raise StationTransferError("Backup expands beyond the 25 GB safety limit")

    manifest_info = entries.get(BACKUP_MANIFEST_NAME)
    if manifest_info is None:
        raise StationTransferError("This is not a Pole Position workstation backup")
    if manifest_info.file_size > MAX_MANIFEST_BYTES:
        raise StationTransferError("Backup manifest is unexpectedly large")
    try:
        manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise StationTransferError(f"Backup manifest is invalid: {exc}") from exc
    if not isinstance(manifest, dict):
        raise StationTransferError("Backup manifest must contain a JSON object")
    if int(manifest.get("schema_version", 0) or 0) != BACKUP_SCHEMA_VERSION:
        raise StationTransferError(
            f"Unsupported backup schema {manifest.get('schema_version')!r}; expected {BACKUP_SCHEMA_VERSION}"
        )
    if str(manifest.get("application", "")) != "Pole Position":
        raise StationTransferError("Backup application identity is not Pole Position")
    return manifest, entries


def _manifest_file_records(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_records = manifest.get("files", [])
    if not isinstance(raw_records, list):
        raise StationTransferError("Backup manifest file list is invalid")
    records: dict[str, dict[str, Any]] = {}
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise StationTransferError("Backup manifest contains an invalid file record")
        name = _safe_archive_path(str(raw.get("path", "")))
        if name in records:
            raise StationTransferError(f"Backup manifest repeats file {name}")
        try:
            size = int(raw.get("size_bytes", -1))
        except (TypeError, ValueError) as exc:
            raise StationTransferError(f"Backup manifest has an invalid size for {name}") from exc
        digest = str(raw.get("sha256", "")).lower()
        if size < 0 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise StationTransferError(f"Backup manifest has invalid integrity data for {name}")
        records[name] = {"path": name, "size_bytes": size, "sha256": digest}
    return records


def inspect_station_backup(source: Path) -> dict[str, Any]:
    """Fully verify an imported archive without writing station data."""

    source = source.expanduser().resolve()
    if not source.is_file():
        raise StationTransferError(f"Backup ZIP was not found: {source}")
    try:
        with zipfile.ZipFile(source, "r") as archive:
            manifest, entries = _read_manifest(archive)
            records = _manifest_file_records(manifest)
            archive_files = set(entries) - {BACKUP_MANIFEST_NAME}
            if archive_files != set(records):
                missing = sorted(set(records) - archive_files)
                extra = sorted(archive_files - set(records))
                raise StationTransferError(
                    f"Backup file list mismatch; missing={missing[:3]}, untracked={extra[:3]}"
                )
            if "config.json" not in records or "runtime/battery_inspector.db" not in records:
                raise StationTransferError("Backup is missing configuration or the recipe database")
            for name, record in records.items():
                info = entries[name]
                if info.file_size != record["size_bytes"]:
                    raise StationTransferError(f"Backup size check failed for {name}")
                with archive.open(info, "r") as handle:
                    digest, size = _hash_stream(handle)
                if size != record["size_bytes"] or digest != record["sha256"]:
                    raise StationTransferError(f"Backup SHA-256 check failed for {name}")
            try:
                config_payload = json.loads(archive.read(entries["config.json"]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StationTransferError(f"Backup configuration is invalid: {exc}") from exc
            if not isinstance(config_payload, dict):
                raise StationTransferError("Backup configuration must contain a JSON object")
    except zipfile.BadZipFile as exc:
        raise StationTransferError(f"Backup ZIP is damaged or invalid: {exc}") from exc

    return {
        "path": str(source),
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at_utc": str(manifest.get("created_at_utc", "")),
        "application_version": str(dict(manifest.get("software") or {}).get("application_version", "")),
        "file_count": len(records),
        "uncompressed_bytes": sum(int(item["size_bytes"]) for item in records.values()),
        "manifest": manifest,
    }


def _extract_verified_backup(source: Path, destination: Path) -> dict[str, Any]:
    inspection = inspect_station_backup(source)
    manifest = dict(inspection["manifest"])
    records = _manifest_file_records(manifest)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(source, "r") as archive:
            for name in records:
                target = destination.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name, "r") as input_file, target.open("wb") as output:
                    shutil.copyfileobj(input_file, output, length=1024 * 1024)
            (destination / BACKUP_MANIFEST_NAME).write_text(
                json.dumps(manifest, indent=2, sort_keys=True),
                encoding="utf-8",
            )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return inspection


def _sqlite_quick_check(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path)) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise StationTransferError(f"Restored recipe database is invalid: {exc}") from exc
    if not result or str(result[0]).lower() != "ok":
        raise StationTransferError("Restored recipe database failed SQLite quick_check")


def stage_station_restore(project_root: Path, source: Path) -> dict[str, Any]:
    """Validate and stage an import. The live station is not changed until restart."""

    project_root = project_root.resolve()
    marker = project_root / PENDING_RESTORE_NAME
    if marker.exists():
        raise StationTransferError(
            "A workstation restore is already pending. Restart Pole Position before importing another backup."
        )
    staging_root = project_root / RESTORE_STAGING_DIRECTORY
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_directory = staging_root / f"restore_{_timestamp()}_{uuid4().hex[:8]}"
    inspection = _extract_verified_backup(source, staging_directory)
    try:
        _sqlite_quick_check(staging_directory / "runtime" / "battery_inspector.db")
        AppConfig.load(staging_directory / "config.json")
        marker_payload = {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "staging_directory": str(staging_directory),
            "source_backup": str(source.expanduser().resolve()),
            "source_sha256": _sha256_file(source.expanduser().resolve()),
            "staged_at_utc": _utc_now(),
        }
        temporary_marker = marker.with_name(f".{marker.name}.{uuid4().hex}.tmp")
        temporary_marker.write_text(json.dumps(marker_payload, indent=2), encoding="utf-8")
        os.replace(temporary_marker, marker)
    except Exception:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise
    return {
        **{key: value for key, value in inspection.items() if key != "manifest"},
        "staging_directory": str(staging_directory),
        "restart_required": True,
    }


def _rebase_path_text(value: str, mappings: list[tuple[str, Path]]) -> str:
    text = str(value)
    for old_root, new_root in mappings:
        old = str(old_root or "").rstrip("/\\")
        if not old:
            continue
        normalized_text = text.replace("\\", "/")
        normalized_old = old.replace("\\", "/")
        lower_text = normalized_text.lower()
        lower_old = normalized_old.lower()
        if lower_text == lower_old:
            return str(new_root)
        prefix = lower_old + "/"
        if lower_text.startswith(prefix):
            suffix = normalized_text[len(normalized_old) :].lstrip("/")
            return str(new_root.joinpath(*PurePosixPath(suffix).parts))
    return text


def _rebase_json_value(value: Any, mappings: list[tuple[str, Path]]) -> Any:
    if isinstance(value, str):
        return _rebase_path_text(value, mappings)
    if isinstance(value, list):
        return [_rebase_json_value(item, mappings) for item in value]
    if isinstance(value, dict):
        return {key: _rebase_json_value(item, mappings) for key, item in value.items()}
    return value


def _rebase_runtime_json(root: Path, mappings: list[tuple[str, Path]]) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
            continue
        try:
            if path.suffix.lower() == ".jsonl":
                lines: list[str] = []
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    lines.append(json.dumps(_rebase_json_value(payload, mappings), sort_keys=True))
                path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
            else:
                payload = json.loads(path.read_text(encoding="utf-8"))
                path.write_text(
                    json.dumps(_rebase_json_value(payload, mappings), indent=2, sort_keys=True),
                    encoding="utf-8",
                )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StationTransferError(f"Could not rebase restored JSON paths in {path}: {exc}") from exc


def _rebase_database(path: Path, mappings: list[tuple[str, Path]]) -> None:
    fields = (
        ("recipes", "payload_json"),
        ("audit_events", "details_json"),
        ("inspections", "payload_json"),
    )
    try:
        # Nested deliberately: the inner context manager commits the UPDATEs,
        # the outer one closes the handle so the restored database can be moved.
        with closing(sqlite3.connect(path)) as connection, connection:
            for table, field in fields:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not exists:
                    continue
                rows = connection.execute(
                    f"SELECT rowid, {field} FROM {table}"  # noqa: S608 - fixed identifiers above
                ).fetchall()
                for rowid, raw in rows:
                    try:
                        payload = json.loads(str(raw or "{}"))
                    except json.JSONDecodeError as exc:
                        raise StationTransferError(
                            f"Restored database contains invalid JSON in {table} row {rowid}"
                        ) from exc
                    rebased = json.dumps(
                        _rebase_json_value(payload, mappings),
                        separators=(",", ":"),
                    )
                    connection.execute(
                        f"UPDATE {table} SET {field} = ? WHERE rowid = ?",  # noqa: S608
                        (rebased, rowid),
                    )
            result = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise StationTransferError(f"Could not update restored database paths: {exc}") from exc
    if not result or str(result[0]).lower() != "ok":
        raise StationTransferError("Rebased database failed SQLite quick_check")


def _portable_ml_target(
    role: str,
    portable: dict[str, Any],
    staging_directory: Path,
    temporary_data: Path,
    target_data: Path,
) -> str | None:
    raw = portable.get(role)
    if not isinstance(raw, dict):
        return None
    archive_name = _safe_archive_path(str(raw.get("archive_path", "")))
    source = staging_directory.joinpath(*PurePosixPath(archive_name).parts)
    if not source.is_file():
        raise StationTransferError(f"Portable ML asset is missing: {archive_name}")
    destination_relative = Path("models") / "restored_backup" / role / source.name
    destination = temporary_data / destination_relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return str(target_data / destination_relative)


def _prepare_restored_config(
    source_config: Path,
    current_config: AppConfig,
    manifest: dict[str, Any],
    staging_directory: Path,
    temporary_data: Path,
    target_data: Path,
    project_root: Path,
) -> tuple[AppConfig, list[tuple[str, Path]]]:
    try:
        payload = json.loads(source_config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StationTransferError(f"Restored configuration is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise StationTransferError("Restored configuration must contain a JSON object")
    source_info = dict(manifest.get("source") or {})
    mappings = [
        (str(source_info.get("data_directory", "")), target_data),
        (str(source_info.get("project_root", "")), project_root),
    ]
    payload = _rebase_json_value(payload, mappings)
    payload["data_directory"] = current_config.data_directory
    ml = payload.get("ml")
    if not isinstance(ml, dict):
        ml = {}
        payload["ml"] = ml
    portable = dict(manifest.get("portable_ml") or {})
    for role in ("model_path", "manifest_path"):
        restored_path = _portable_ml_target(
            role,
            portable,
            staging_directory,
            temporary_data,
            target_data,
        )
        if restored_path:
            ml[role] = restored_path
    temporary_config = staging_directory / ".rebased_config.json"
    temporary_config.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return AppConfig.load(temporary_config), mappings


def apply_pending_restore(project_root: Path, config_path: Path) -> dict[str, Any]:
    """Apply a previously validated restore before services or SQLite are opened."""

    project_root = project_root.resolve()
    config_path = config_path.resolve()
    marker = project_root / PENDING_RESTORE_NAME
    if not marker.is_file():
        return {}
    try:
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        staging_directory = Path(str(marker_payload["staging_directory"])).resolve()
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise StationTransferError(f"Pending restore marker is invalid: {exc}") from exc
    staging_root = (project_root / RESTORE_STAGING_DIRECTORY).resolve()
    if not _is_relative_to(staging_directory, staging_root) or not staging_directory.is_dir():
        raise StationTransferError("Pending restore staging directory is outside the controlled restore area")

    manifest_path = staging_directory / BACKUP_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StationTransferError(f"Staged restore manifest is invalid: {exc}") from exc
    records = _manifest_file_records(manifest)
    for name, record in records.items():
        source = staging_directory.joinpath(*PurePosixPath(name).parts)
        if not source.is_file():
            raise StationTransferError(f"Staged restore file is missing: {name}")
        if source.stat().st_size != record["size_bytes"] or _sha256_file(source) != record["sha256"]:
            raise StationTransferError(f"Staged restore integrity check failed: {name}")

    if config_path.is_file():
        current_config = AppConfig.load(config_path)
    else:
        current_config = AppConfig.default()
        current_config.save(config_path)
    target_data = current_config.resolved_data_directory(project_root).resolve()
    if target_data in {Path(target_data.anchor), project_root}:
        raise StationTransferError(f"Unsafe restore data-directory target: {target_data}")

    rollback_directory = project_root / ROLLBACK_DIRECTORY
    rollback_directory.mkdir(parents=True, exist_ok=True)
    rollback_zip = rollback_directory / (
        f"Pole_Position_PreRestore_{_timestamp()}_{uuid4().hex[:8]}.zip"
    )
    create_station_backup(project_root, config_path, target_data, rollback_zip)

    temporary_data = target_data.parent / f".{target_data.name}.restore_{uuid4().hex}"
    previous_data = target_data.parent / f".{target_data.name}.pre_restore_{uuid4().hex}"
    temporary_config = config_path.with_name(f".{config_path.name}.restore_{uuid4().hex}")
    data_swapped = False
    try:
        shutil.copytree(staging_directory / "runtime", temporary_data)
        restored_config, mappings = _prepare_restored_config(
            staging_directory / "config.json",
            current_config,
            manifest,
            staging_directory,
            temporary_data,
            target_data,
            project_root,
        )
        _rebase_runtime_json(temporary_data, mappings)
        _rebase_database(temporary_data / "battery_inspector.db", mappings)
        restored_config.save(temporary_config)

        if target_data.exists():
            os.replace(target_data, previous_data)
        os.replace(temporary_data, target_data)
        data_swapped = True
        os.replace(temporary_config, config_path)
    except Exception:
        temporary_config.unlink(missing_ok=True)
        shutil.rmtree(temporary_data, ignore_errors=True)
        if data_swapped:
            shutil.rmtree(target_data, ignore_errors=True)
        if previous_data.exists() and not target_data.exists():
            os.replace(previous_data, target_data)
        raise
    else:
        shutil.rmtree(previous_data, ignore_errors=True)
        marker.unlink(missing_ok=True)
        shutil.rmtree(staging_directory, ignore_errors=True)
        result = {
            "status": "restored",
            "restored_at_utc": _utc_now(),
            "source_backup": str(marker_payload.get("source_backup", "")),
            "source_application_version": str(
                dict(manifest.get("software") or {}).get("application_version", "")
            ),
            "rollback_backup": str(rollback_zip),
            "data_directory": str(target_data),
        }
        (project_root / RESTORE_RESULT_NAME).write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )
        return result
