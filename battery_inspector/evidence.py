from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import cv2
import numpy as np

from battery_inspector.models import ReferenceCapture

if TYPE_CHECKING:
    from battery_inspector.services.camera import CameraFrame


class EvidenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FailureRetentionPolicy:
    """Limits for production non-PASS evidence only.

    A value of zero disables the corresponding age or capacity limit. PASS
    evidence is always removed because production PASS cycles are memory-only.
    """

    max_age_days: int = 30
    max_bytes: int = 5 * 1024**3


@dataclass(frozen=True, slots=True)
class FailureRetentionReport:
    pass_cycles_removed: int = 0
    expired_cycles_removed: int = 0
    capacity_cycles_removed: int = 0
    bytes_removed: int = 0
    bytes_remaining: int = 0
    failure_cycles_remaining: int = 0


@dataclass(frozen=True, slots=True)
class _EvidenceCycle:
    directory: Path
    disposition: str
    timestamp: datetime
    size_bytes: int


def _cycle_size(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _manifest_timestamp(payload: dict[str, Any], directory: Path) -> datetime:
    result = payload.get("result")
    text = str(result.get("timestamp_utc", "")) if isinstance(result, dict) else ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc)
        except OSError:
            return datetime.now(timezone.utc)


def _production_evidence_cycles(root: Path) -> list[_EvidenceCycle]:
    if not root.is_dir() or root.is_symlink():
        return []
    cycles: list[_EvidenceCycle] = []
    for day in sorted(root.iterdir()):
        if not day.is_dir() or day.is_symlink():
            continue
        for directory in sorted(day.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            manifest = directory / "manifest.json"
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                # Leave incomplete/unknown directories untouched. Retention may
                # delete only evidence it can positively identify.
                continue
            result = payload.get("result")
            disposition = (
                str(result.get("disposition", "")).strip().lower()
                if isinstance(result, dict)
                else ""
            )
            if not disposition:
                continue
            cycles.append(
                _EvidenceCycle(
                    directory=directory,
                    disposition=disposition,
                    timestamp=_manifest_timestamp(payload, directory),
                    size_bytes=_cycle_size(directory),
                )
            )
    return cycles


def _remove_cycle(root: Path, cycle: _EvidenceCycle) -> bool:
    try:
        resolved_root = root.resolve()
        resolved_cycle = cycle.directory.resolve()
        if resolved_cycle.parent.parent != resolved_root:
            return False
        shutil.rmtree(resolved_cycle)
        return True
    except OSError:
        return False


def apply_failure_retention(
    inspections_root: Path,
    policy: FailureRetentionPolicy,
    *,
    now: datetime | None = None,
) -> FailureRetentionReport:
    """Purge production PASS artifacts and bound retained non-PASS evidence.

    Only two-level cycle directories beneath ``inspections_root`` are eligible.
    Validation, recipe, model, and training directories are never traversed.
    The newest failure is preserved for capacity enforcement even when that one
    package is larger than the configured maximum.
    """

    root = inspections_root.expanduser()
    cycles = _production_evidence_cycles(root)
    pass_removed = expired_removed = capacity_removed = bytes_removed = 0

    failures: list[_EvidenceCycle] = []
    for cycle in cycles:
        if cycle.disposition == "pass":
            if _remove_cycle(root, cycle):
                pass_removed += 1
                bytes_removed += cycle.size_bytes
        else:
            failures.append(cycle)

    utc_now = now or datetime.now(timezone.utc)
    if utc_now.tzinfo is None:
        utc_now = utc_now.replace(tzinfo=timezone.utc)
    else:
        utc_now = utc_now.astimezone(timezone.utc)
    max_age_days = max(0, int(policy.max_age_days))
    if max_age_days:
        cutoff = utc_now - timedelta(days=max_age_days)
        retained: list[_EvidenceCycle] = []
        for cycle in failures:
            if cycle.timestamp < cutoff and _remove_cycle(root, cycle):
                expired_removed += 1
                bytes_removed += cycle.size_bytes
            else:
                retained.append(cycle)
        failures = retained

    failures.sort(key=lambda item: (item.timestamp, str(item.directory)))
    total = sum(item.size_bytes for item in failures)
    max_bytes = max(0, int(policy.max_bytes))
    # The final (newest) item is intentionally not a capacity candidate.
    for cycle in list(failures[:-1]) if max_bytes else []:
        if total <= max_bytes:
            break
        if _remove_cycle(root, cycle):
            failures.remove(cycle)
            capacity_removed += 1
            bytes_removed += cycle.size_bytes
            total -= cycle.size_bytes

    if root.is_dir():
        for day in list(root.iterdir()):
            try:
                if day.is_dir() and not day.is_symlink() and not any(day.iterdir()):
                    day.rmdir()
            except OSError:
                continue

    return FailureRetentionReport(
        pass_cycles_removed=pass_removed,
        expired_cycles_removed=expired_removed,
        capacity_cycles_removed=capacity_removed,
        bytes_removed=bytes_removed,
        bytes_remaining=max(0, total),
        failure_cycles_remaining=len(failures),
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_png(path: Path, image: np.ndarray, *, compression: int = 1) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp.png")
    parameters = [cv2.IMWRITE_PNG_COMPRESSION, int(max(0, min(compression, 9)))]
    if not cv2.imwrite(str(temporary), image, parameters):
        raise EvidenceError(f"Could not write image evidence: {path}")
    temporary.replace(path)
    return str(path)


def save_jpeg(path: Path, image: np.ndarray, *, quality: int = 95) -> str:
    """Atomically save display/evidence imagery with bounded cycle-time cost.

    Inspection decisions always use the in-memory camera frame. JPEG is used only
    for retained operator evidence; high-resolution PNG compression can otherwise
    dominate the cycle time for a 20 MP camera. Recipe reference captures remain
    lossless PNG files because they are long-lived registration inputs.
    """

    path = path.with_suffix(".jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp.jpg")
    parameters = [
        cv2.IMWRITE_JPEG_QUALITY,
        int(max(70, min(quality, 100))),
        cv2.IMWRITE_JPEG_OPTIMIZE,
        0,
    ]
    if not cv2.imwrite(str(temporary), image, parameters):
        raise EvidenceError(f"Could not write image evidence: {path}")
    temporary.replace(path)
    return str(path)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return str(path)


def copy_reference_image(source: Path, destination: Path) -> str:
    if not source.is_file():
        raise EvidenceError(f"Reference image was not found: {source}")
    try:
        if source.resolve() == destination.resolve():
            return str(destination)
    except OSError:
        pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)
    return str(destination)



def export_evidence_package(
    evidence_directory: Path,
    destination: Path,
) -> Path:
    """Create an atomic ZIP containing one complete inspection evidence folder."""

    source = evidence_directory.expanduser().resolve()
    if not source.is_dir():
        raise EvidenceError(f"Inspection evidence directory was not found: {source}")

    destination = destination.expanduser()
    if destination.suffix.lower() != ".zip":
        destination = destination.with_suffix(".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination_resolved = destination.resolve()
    except OSError:
        destination_resolved = destination.absolute()
    temporary = destination.with_name(
        f".{destination.stem}.{uuid4().hex}.tmp.zip"
    )

    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise EvidenceError(f"Inspection evidence directory is empty: {source}")

    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for path in files:
                try:
                    resolved = path.resolve()
                except OSError:
                    resolved = path.absolute()
                if resolved in {destination_resolved, temporary.resolve()}:
                    continue
                archive.write(path, arcname=str(path.relative_to(source.parent)))
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination

def assess_image_quality(image: np.ndarray) -> dict[str, Any]:
    if image.size == 0:
        return {
            "status": "POOR",
            "reason": "EMPTY_IMAGE",
            "mean_level": 0.0,
            "sharpness": 0.0,
            "dark_fraction": 1.0,
            "clipped_fraction": 0.0,
        }

    if image.ndim == 2:
        gray = image
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mean_level = float(np.mean(gray))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    dark_fraction = float(np.count_nonzero(gray <= 8)) / float(gray.size)
    clipped_fraction = float(np.count_nonzero(gray >= 247)) / float(gray.size)

    status = "GOOD"
    reasons: list[str] = []
    if mean_level < 8 or mean_level > 247:
        status = "POOR"
        reasons.append("EXTREME_EXPOSURE")
    elif mean_level < 22 or mean_level > 225:
        status = "WARNING"
        reasons.append("EXPOSURE_MARGIN")
    if dark_fraction > 0.85 or clipped_fraction > 0.85:
        status = "POOR"
        reasons.append("HEAVY_CLIPPING")
    if sharpness < 3.0:
        status = "POOR"
        reasons.append("LOW_SHARPNESS")
    elif sharpness < 12.0 and status == "GOOD":
        status = "WARNING"
        reasons.append("SHARPNESS_MARGIN")

    return {
        "status": status,
        "reason": ",".join(reasons) if reasons else "OK",
        "mean_level": mean_level,
        "sharpness": sharpness,
        "dark_fraction": dark_fraction,
        "clipped_fraction": clipped_fraction,
        "width_px": int(image.shape[1]),
        "height_px": int(image.shape[0]),
        "channels": int(image.shape[2]) if image.ndim == 3 else 1,
    }


def reference_capture_from_file(
    path: Path,
    *,
    source: str,
    captured_at_utc: str | None = None,
    capture_id: str | None = None,
    frame_sequence: int = 0,
    frame_id: str = "",
    camera_frame_id: str = "",
    camera_timestamp_raw: int | None = None,
    camera_backend: str = "",
    camera_description: str = "",
    camera_profile: dict[str, Any] | None = None,
) -> ReferenceCapture:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise EvidenceError(f"Reference image could not be opened: {path}")
    height, width = image.shape[:2]
    channels = int(image.shape[2]) if image.ndim == 3 else 1
    return ReferenceCapture(
        capture_id=capture_id or str(uuid4()),
        path=str(path),
        sha256=sha256_file(path),
        captured_at_utc=captured_at_utc or utc_now_iso(),
        width_px=int(width),
        height_px=int(height),
        channels=channels,
        frame_sequence=frame_sequence,
        frame_id=frame_id,
        camera_frame_id=camera_frame_id,
        camera_timestamp_raw=camera_timestamp_raw,
        source=source,
        camera_backend=camera_backend,
        camera_description=camera_description,
        camera_profile=dict(camera_profile or {}),
        quality=assess_image_quality(image),
    )


def stage_reference_capture(
    frame: "CameraFrame",
    staging_directory: Path,
    *,
    source: str = "RECIPE_WIZARD",
    camera_profile: dict[str, Any] | None = None,
) -> ReferenceCapture:
    """Save one freshly acquired frame for review in the recipe wizard."""

    # Local import avoids making the evidence module a camera dependency at import time.
    from battery_inspector.services.camera import CameraFrame

    if not isinstance(frame, CameraFrame):
        raise EvidenceError("Reference capture requires CameraFrame metadata")
    if not frame.fresh:
        raise EvidenceError("The reference frame failed freshness validation")
    capture_id = str(uuid4())
    destination = staging_directory / f"reference-{capture_id}.png"
    save_png(destination, frame.image)
    description = frame.device.display_name if frame.device else ""
    reference = reference_capture_from_file(
        destination,
        source=source,
        captured_at_utc=frame.captured_at_utc,
        capture_id=capture_id,
        frame_sequence=frame.sequence,
        frame_id=frame.frame_id,
        camera_frame_id=frame.camera_frame_id,
        camera_timestamp_raw=frame.camera_timestamp_raw,
        camera_backend=frame.backend_name,
        camera_description=description,
        camera_profile=camera_profile,
    )
    write_json_atomic(
        destination.with_suffix(".json"),
        {
            "schema_version": 1,
            "reference": reference.to_dict(),
            "camera_frame": frame.metadata(),
        },
    )
    return reference


def persist_recipe_reference(
    reference: ReferenceCapture,
    data_directory: Path,
    *,
    recipe_id: str,
    revision: int,
) -> ReferenceCapture:
    """Copy an accepted reference into an immutable recipe-revision directory."""

    source = Path(reference.path)
    if not source.is_file():
        raise EvidenceError(f"Accepted reference image was not found: {source}")
    source_hash = sha256_file(source)
    if reference.sha256 and source_hash.lower() != reference.sha256.lower():
        raise EvidenceError(
            "Accepted reference image changed after it was captured; retake or reselect the reference."
        )
    suffix = source.suffix.lower()
    if suffix not in {".png", ".bmp", ".jpg", ".jpeg", ".tif", ".tiff"}:
        suffix = ".png"
    destination = (
        data_directory
        / "recipes"
        / recipe_id
        / f"revision_{revision:04d}"
        / f"reference{suffix}"
    )
    try:
        same_file = source.resolve() == destination.resolve()
    except OSError:
        same_file = False
    if not same_file:
        copy_reference_image(source, destination)
    destination_hash = sha256_file(destination)
    if destination_hash.lower() != source_hash.lower():
        raise EvidenceError("Persisted recipe reference failed SHA-256 verification")
    persisted = reference_capture_from_file(
        destination,
        source=reference.source,
        captured_at_utc=reference.captured_at_utc,
        capture_id=reference.capture_id,
        frame_sequence=reference.frame_sequence,
        frame_id=reference.frame_id,
        camera_frame_id=reference.camera_frame_id,
        camera_timestamp_raw=reference.camera_timestamp_raw,
        camera_backend=reference.camera_backend,
        camera_description=reference.camera_description,
        camera_profile=reference.camera_profile,
    )
    # Keep the quality assessment made at capture time if it existed. It may include
    # future station-specific checks beyond the generic image metrics.
    if reference.quality:
        persisted.quality = dict(reference.quality)
    write_json_atomic(
        destination.with_name("reference.json"),
        {
            "schema_version": 1,
            "recipe_id": recipe_id,
            "revision": int(revision),
            "reference": persisted.to_dict(),
        },
    )
    return persisted


def persist_recipe_validation_records(
    records: list[dict[str, Any]],
    data_directory: Path,
    *,
    recipe_id: str,
    revision: int,
    configuration_hash: str = "",
) -> list[dict[str, Any]]:
    """Persist validation-template crops with the immutable recipe revision.

    Runtime validation evidence may later be archived by the station retention
    policy. The recipe classifier must not silently lose its commissioned
    examples, so passing terminal and marking crops are copied into the recipe
    revision and the stored record paths are rewritten to those immutable files.
    Failed/duplicate records remain as metadata but do not become templates.
    """

    root = (
        data_directory
        / "recipes"
        / recipe_id
        / f"revision_{int(revision):04d}"
        / "validation_templates"
    )
    persisted_records: list[dict[str, Any]] = []
    for sample_index, original in enumerate(records, start=1):
        if not isinstance(original, dict):
            continue
        record = deepcopy(original)
        record_hash = str(record.get("configuration_hash", ""))
        eligible = str(record.get("disposition", "")).lower() == "pass"
        if configuration_hash and record_hash and record_hash != configuration_hash:
            eligible = False
        record["original_evidence_directory"] = str(
            record.get("original_evidence_directory")
            or record.get("evidence_directory")
            or ""
        )
        if eligible:
            sample_dir = root / f"sample_{sample_index:03d}"
            terminals: list[dict[str, Any]] = []
            for payload in list(record.get("terminals", []) or []):
                if not isinstance(payload, dict):
                    continue
                terminal = deepcopy(payload)
                terminal_key = str(terminal.get("terminal_key", "terminal"))
                for field_name, suffix in (
                    ("terminal_crop_path", "terminal"),
                    ("marking_crop_path", "marking"),
                    ("reference_marking_path", "reference_marking"),
                ):
                    source_text = str(terminal.get(field_name, "") or "")
                    source = Path(source_text)
                    if not source.is_file():
                        continue
                    extension = source.suffix.lower()
                    if extension not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
                        extension = ".png"
                    destination = sample_dir / f"{terminal_key}_{suffix}{extension}"
                    copy_reference_image(source, destination)
                    if sha256_file(source) != sha256_file(destination):
                        raise EvidenceError(
                            f"Persisted validation crop failed SHA-256 verification: {source}"
                        )
                    terminal[field_name] = str(destination)
                terminals.append(terminal)
            record["terminals"] = terminals
            record["persisted_template_directory"] = str(sample_dir)
        persisted_records.append(record)

    write_json_atomic(
        root.parent / "validation.json",
        {
            "schema_version": 1,
            "recipe_id": recipe_id,
            "revision": int(revision),
            "configuration_hash": configuration_hash,
            "records": persisted_records,
        },
    )
    return persisted_records
