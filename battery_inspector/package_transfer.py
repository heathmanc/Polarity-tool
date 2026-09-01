"""Move one recipe, or one model, from station to station.

The workstation backup already moves everything, and it is the right tool for
replacing a machine. It is the wrong tool for the two things a technician
actually does between machines: send a trained model to another station, and
put one product's recipe on a second line. Both used to mean either exporting
the whole station or re-teaching from scratch.

Two package kinds, both ZIPs, both carrying a manifest that names every member
and its SHA-256:

* **Model package** -- the ONNX and manifest a station is inspecting with, so a
  second station can install exactly that model and resolve recipes bound to
  its hash.
* **Recipe package** -- one recipe revision with its reference image, its
  validation evidence, and (when it is ML-bound) the model it is bound to, so
  it can grade parts on the destination without being re-taught.

A recipe package carries validation evidence across machines, by explicit
product decision. That evidence was taken on the source station's camera,
lens, and lighting, and nothing here can confirm the destination's are the
same -- so the package records where it came from and what it was validated
against, the import is confirmed by a technician, and both sides are written to
the audit log. What it cannot do is make a recipe gradeable without its model:
a station missing the bound model still reports an unusable binding and refuses
to inspect until that model is installed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from battery_inspector.build_info import INSPECTION_ENGINE, software_build_info
from battery_inspector.models import Recipe
from battery_inspector.station_transfer import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_FILES,
    MAX_MANIFEST_BYTES,
    StationTransferError,
    _safe_archive_path,
)


PACKAGE_SCHEMA_VERSION = 1
MODEL_MANIFEST_NAME = "pole_position_model_package.json"
RECIPE_MANIFEST_NAME = "pole_position_recipe_package.json"
MODEL_KIND = "model"
RECIPE_KIND = "recipe"


class PackageTransferError(StationTransferError):
    """Raised when a model or recipe package cannot be written or trusted."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_component(value: str, fallback: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in str(value or "").strip()
    )
    return cleaned or fallback


def _write_package(
    destination: Path,
    manifest_name: str,
    manifest: dict[str, Any],
    members: list[tuple[str, Path]],
) -> dict[str, Any]:
    """Write members and a manifest that names each one's SHA-256.

    Written to a temporary file and moved into place, so an interrupted export
    never leaves a half-written archive looking like a package.
    """

    destination = destination.expanduser().resolve()
    if destination.suffix.lower() != ".zip":
        destination = destination.with_suffix(".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")

    files: list[dict[str, Any]] = []
    for archive_name, source in members:
        if not source.is_file():
            raise PackageTransferError(f"Package member is missing: {source}")
        files.append(
            {
                "path": _safe_archive_path(archive_name),
                "sha256": _sha256_file(source),
                "bytes": source.stat().st_size,
            }
        )
    manifest = dict(manifest)
    manifest.update(
        {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "created_at_utc": _utc_now(),
            "inspection_engine": INSPECTION_ENGINE,
            "software": software_build_info(),
            "files": files,
        }
    )

    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(manifest_name, json.dumps(manifest, indent=2))
            for archive_name, source in members:
                archive.write(source, _safe_archive_path(archive_name))
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"path": str(destination), "manifest": manifest}


def _read_manifest(archive: zipfile.ZipFile, manifest_name: str) -> dict[str, Any]:
    names = archive.namelist()
    if len(names) > MAX_ARCHIVE_FILES:
        raise PackageTransferError("Package contains too many members to be a station package")
    if sum(max(0, item.file_size) for item in archive.infolist()) > MAX_ARCHIVE_BYTES:
        raise PackageTransferError("Package expands to more data than a station package may carry")
    if manifest_name not in names:
        raise PackageTransferError(
            f"This ZIP is not a Pole Position package: {manifest_name} is missing"
        )
    info = archive.getinfo(manifest_name)
    if info.file_size > MAX_MANIFEST_BYTES:
        raise PackageTransferError("Package manifest is implausibly large")
    try:
        manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PackageTransferError(f"Package manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PackageTransferError("Package manifest must contain a JSON object")
    if int(manifest.get("schema_version", 0)) > PACKAGE_SCHEMA_VERSION:
        raise PackageTransferError(
            "This package was written by a newer release of Pole Position. "
            "Upgrade this station before importing it."
        )
    return manifest


def _extract_verified(
    archive: zipfile.ZipFile,
    manifest: dict[str, Any],
    destination: Path,
) -> dict[str, Path]:
    """Extract every member the manifest names, refusing any hash mismatch.

    Only manifest members are extracted, and each is written under a path
    normalized by ``_safe_archive_path``, so nothing in the archive can place a
    file outside the destination.
    """

    destination.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise PackageTransferError("Package manifest lists no files")
    for entry in entries:
        if not isinstance(entry, dict):
            raise PackageTransferError("Package manifest contains a malformed file entry")
        name = _safe_archive_path(str(entry.get("path", "")))
        expected = str(entry.get("sha256", "")).strip().lower()
        if not expected:
            raise PackageTransferError(f"Package manifest does not checksum {name}")
        try:
            data = archive.read(name)
        except KeyError as exc:
            raise PackageTransferError(f"Package is missing a file it declares: {name}") from exc
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise PackageTransferError(
                f"{name} does not match its checksum. The package is damaged or was "
                "modified after it was written; export it again."
            )
        target = destination / Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written[name] = target
    return written


# --- model packages ---------------------------------------------------------


def export_model_package(
    *,
    model_path: Path,
    manifest_path: Path,
    destination: Path,
    station_name: str = "",
) -> dict[str, Any]:
    """Package the model a station is inspecting with."""

    model_path = Path(model_path)
    manifest_path = Path(manifest_path)
    if not model_path.is_file():
        raise PackageTransferError(f"The station ONNX model was not found: {model_path}")
    if not manifest_path.is_file():
        raise PackageTransferError(f"The station model manifest was not found: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageTransferError(f"The station model manifest is not valid JSON: {exc}") from exc

    manifest = {
        "kind": MODEL_KIND,
        "source_station": station_name,
        "model_id": str(payload.get("model_id", "") or ""),
        "model_version": str(payload.get("model_version", "") or ""),
        "model_sha256": _sha256_file(model_path),
        "classes": list(payload.get("classes", []) or []),
        "input_crop_contract": str(payload.get("input_crop_contract", "") or ""),
    }
    return _write_package(
        destination,
        MODEL_MANIFEST_NAME,
        manifest,
        [
            ("model/polarity_classifier.onnx", model_path),
            ("model/polarity_classifier.json", manifest_path),
        ],
    )


def inspect_model_package(source: Path) -> dict[str, Any]:
    """What a model package says it holds, without writing anything."""

    with zipfile.ZipFile(Path(source)) as archive:
        manifest = _read_manifest(archive, MODEL_MANIFEST_NAME)
    if str(manifest.get("kind", "")) != MODEL_KIND:
        raise PackageTransferError("This package is not a Pole Position model package")
    return manifest


def import_model_package(source: Path, models_root: Path) -> dict[str, Any]:
    """Verify a model package and lay it down under the station's models root.

    Returns the manifest plus the installed paths. Installing it as the
    station's model is the caller's decision, not this function's.
    """

    source = Path(source)
    with zipfile.ZipFile(source) as archive:
        manifest = _read_manifest(archive, MODEL_MANIFEST_NAME)
        if str(manifest.get("kind", "")) != MODEL_KIND:
            raise PackageTransferError("This package is not a Pole Position model package")
        destination = (
            Path(models_root)
            / _safe_component(str(manifest.get("model_id", "")), "imported-model")
            / _safe_component(str(manifest.get("model_version", "")), "imported")
        )
        written = _extract_verified(archive, manifest, destination)

    model_file = written.get("model/polarity_classifier.onnx")
    manifest_file = written.get("model/polarity_classifier.json")
    if model_file is None or manifest_file is None:
        raise PackageTransferError("The model package did not contain a model and a manifest")
    return {
        "manifest": manifest,
        "model_path": str(model_file),
        "manifest_path": str(manifest_file),
        "model_sha256": _sha256_file(model_file),
    }


# --- failure evidence packages ----------------------------------------------

FAILURE_MANIFEST_NAME = "pole_position_failure_export.json"
FAILURE_KIND = "failures"


def export_failure_package(
    *,
    records: list[dict[str, Any]],
    destination: Path,
    station_name: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Package a set of retained failures for quality to look at elsewhere.

    One ZIP holding each record's evidence folder plus a summary index, so a
    reviewer opening it sees what rejected and why without the station.
    """

    if not records:
        raise PackageTransferError("No failures were selected to export")

    members: list[tuple[str, Path]] = []
    index: list[dict[str, Any]] = []
    missing: list[str] = []
    for record in records:
        inspection_id = str(record.get("inspection_id", "") or "")
        directory = Path(str(record.get("evidence_directory", "") or ""))
        entry = {
            "inspection_id": inspection_id,
            "timestamp_utc": str(record.get("timestamp_utc", "")),
            "recipe_name": str(record.get("recipe_name", "")),
            "disposition": str(record.get("disposition", "")),
            "reason": str(record.get("reason", "")),
            "review_state": str(record.get("review_state", "")),
        }
        if not inspection_id or not directory.is_dir():
            # Retention may have removed the folder while the row survives.
            # Say so in the index rather than dropping the record silently.
            entry["evidence"] = "MISSING"
            missing.append(inspection_id or "unknown")
            index.append(entry)
            continue
        folder = f"failures/{_safe_component(inspection_id, 'inspection')}"
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            members.append((f"{folder}/{path.relative_to(directory).as_posix()}", path))
        entry["evidence"] = folder
        index.append(entry)

    if not members:
        raise PackageTransferError(
            "None of the selected failures still have evidence on this station. "
            "Retention may have removed it."
        )

    manifest = {
        "kind": FAILURE_KIND,
        "source_station": station_name,
        "description": description,
        "record_count": len(index),
        "evidence_missing": missing,
        "records": index,
    }
    return _write_package(destination, FAILURE_MANIFEST_NAME, manifest, members)


# --- recipe packages --------------------------------------------------------


def export_recipe_package(
    *,
    recipe: Recipe,
    destination: Path,
    model_path: Path | None = None,
    model_manifest_path: Path | None = None,
    station_name: str = "",
) -> dict[str, Any]:
    """Package one recipe revision with everything needed to run it elsewhere.

    The reference image is required: a recipe without one cannot grade a part,
    and a package that silently omitted it would look complete and be useless.
    """

    reference = recipe.reference_image
    if reference is None or not str(reference.path or "").strip():
        raise PackageTransferError(
            f"{recipe.name} revision {recipe.revision} has no reference image, so there "
            "is nothing to move. Capture and accept a reference first."
        )
    reference_path = Path(reference.path)
    if not reference_path.is_file():
        raise PackageTransferError(
            f"The reference image for {recipe.name} revision {recipe.revision} is missing "
            f"from this station: {reference_path}"
        )

    payload = recipe.to_dict()
    members: list[tuple[str, Path]] = [
        (f"reference/{reference_path.name}", reference_path),
    ]
    binding = recipe.classifier_settings.normalized()
    included_model = False
    if model_path is not None and model_manifest_path is not None:
        model_path = Path(model_path)
        model_manifest_path = Path(model_manifest_path)
        if model_path.is_file() and model_manifest_path.is_file():
            members.append(("model/polarity_classifier.onnx", model_path))
            members.append(("model/polarity_classifier.json", model_manifest_path))
            included_model = True

    manifest = {
        "kind": RECIPE_KIND,
        "source_station": station_name,
        "recipe_id": recipe.recipe_id,
        "recipe_number": int(recipe.recipe_number),
        "recipe_name": recipe.name,
        "part_number": recipe.part_number,
        "revision": int(recipe.revision),
        "status": recipe.status.value,
        "validation_runs_required": int(recipe.validation_runs_required),
        "validation_runs_passed": int(recipe.validation_runs_passed),
        "validation_complete": bool(recipe.validation_complete),
        "ml_model_sha256": str(binding.ml_model_sha256 or ""),
        "ml_model_id": str(binding.ml_model_id or ""),
        "ml_model_version": str(binding.ml_model_version or ""),
        "includes_model": included_model,
        "reference_sha256": str(reference.sha256 or ""),
        "reference_file": f"reference/{reference_path.name}",
        "recipe": payload,
    }
    return _write_package(destination, RECIPE_MANIFEST_NAME, manifest, members)


def inspect_recipe_package(source: Path) -> dict[str, Any]:
    """What a recipe package says it holds, without writing anything.

    The import dialog is built from this: a technician is told which station
    the evidence came from and what model it was validated against before
    deciding to trust it.
    """

    with zipfile.ZipFile(Path(source)) as archive:
        manifest = _read_manifest(archive, RECIPE_MANIFEST_NAME)
    if str(manifest.get("kind", "")) != RECIPE_KIND:
        raise PackageTransferError("This package is not a Pole Position recipe package")
    if not isinstance(manifest.get("recipe"), dict):
        raise PackageTransferError("The recipe package does not contain a recipe")
    return manifest


def import_recipe_package(
    source: Path,
    *,
    reference_root: Path,
    models_root: Path,
) -> dict[str, Any]:
    """Verify a recipe package and lay its assets down on this station.

    Returns the recipe -- repointed at the reference image as written here --
    and the model package if one travelled with it. Saving the recipe and
    installing the model are the caller's decisions.
    """

    source = Path(source)
    with zipfile.ZipFile(source) as archive:
        manifest = _read_manifest(archive, RECIPE_MANIFEST_NAME)
        if str(manifest.get("kind", "")) != RECIPE_KIND:
            raise PackageTransferError("This package is not a Pole Position recipe package")
        payload = manifest.get("recipe")
        if not isinstance(payload, dict):
            raise PackageTransferError("The recipe package does not contain a recipe")
        staging = Path(reference_root) / "imported" / uuid4().hex
        written = _extract_verified(archive, manifest, staging)

    recipe = Recipe.from_dict(payload)
    reference_name = str(manifest.get("reference_file", "") or "")
    reference_file = written.get(reference_name)
    if reference_file is None:
        shutil.rmtree(staging, ignore_errors=True)
        raise PackageTransferError("The recipe package did not contain its reference image")
    if recipe.reference_image is None:
        shutil.rmtree(staging, ignore_errors=True)
        raise PackageTransferError("The packaged recipe carries no reference record")

    declared = str(recipe.reference_image.sha256 or "").strip().lower()
    actual = _sha256_file(reference_file)
    if declared and declared != actual:
        shutil.rmtree(staging, ignore_errors=True)
        raise PackageTransferError(
            "The reference image does not match the hash recorded in the recipe. "
            "The package is damaged; export it again."
        )
    recipe.reference_image.path = str(reference_file)

    model: dict[str, Any] | None = None
    model_file = written.get("model/polarity_classifier.onnx")
    model_manifest = written.get("model/polarity_classifier.json")
    if model_file is not None and model_manifest is not None:
        destination = (
            Path(models_root)
            / _safe_component(str(manifest.get("ml_model_id", "")), "imported-model")
            / _safe_component(str(manifest.get("ml_model_version", "")), "imported")
        )
        destination.mkdir(parents=True, exist_ok=True)
        installed_model = destination / "polarity_classifier.onnx"
        installed_manifest = destination / "polarity_classifier.json"
        shutil.copy2(model_file, installed_model)
        shutil.copy2(model_manifest, installed_manifest)
        model = {
            "model_path": str(installed_model),
            "manifest_path": str(installed_manifest),
            "model_sha256": _sha256_file(installed_model),
        }

    return {"manifest": manifest, "recipe": recipe, "model": model}
