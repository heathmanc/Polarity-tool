from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable


SUPPORTED_LABELS = {"plus", "minus", "blank", "invalid_marking"}


@dataclass(slots=True)
class DatasetExportRecord:
    label: str
    destination_path: str
    source_path: str
    sha256: str
    recipe_id: str
    recipe_name: str
    recipe_revision: int
    inspection_id: str
    cycle_id: str
    captured_at_utc: str
    terminal_key: str
    terminal_role: str
    expected_marking: str
    detected_marking: str
    marking_confidence: float
    classifier_status: str
    validation_mode: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_evidence_path(path_text: str, manifest_path: Path) -> Path:
    candidate = Path(path_text)
    if candidate.is_absolute():
        if candidate.is_file():
            return candidate
        local_name = PureWindowsPath(path_text).name
        fallback = manifest_path.parent / local_name
        if fallback.is_file():
            return fallback
        return candidate
    direct = manifest_path.parent / candidate
    if direct.is_file():
        return direct
    # Exported Windows evidence packages may contain absolute C:\... paths in
    # their manifests.  When reviewed on another station/OS, fall back to the
    # basename beside the manifest.
    local_name = PureWindowsPath(path_text).name
    fallback = manifest_path.parent / local_name
    if fallback.is_file():
        return fallback
    # Older records may have been written relative to the application root.
    return candidate


def discover_manifests(
    data_directory: Path,
    *,
    include_production_passes: bool = False,
) -> list[Path]:
    roots = [data_directory / "validation"]
    if include_production_passes:
        roots.append(data_directory / "inspections")
    manifests: list[Path] = []
    for root in roots:
        if root.is_dir():
            manifests.extend(root.rglob("manifest.json"))
    return sorted(set(path.resolve() for path in manifests))


def _eligible_terminal_records(
    manifest_path: Path,
    payload: dict[str, Any],
    *,
    include_production_passes: bool,
    allow_legacy_marking_crops: bool,
) -> Iterable[tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]]:
    result = dict(payload.get("result") or {})
    if str(result.get("disposition", "")).strip().lower() != "pass":
        return
    validation_mode = bool(payload.get("validation_mode", False))
    if not validation_mode and not include_production_passes:
        return
    recipe = dict(payload.get("recipe") or {})
    for terminal in list(result.get("terminals") or []):
        if not isinstance(terminal, dict):
            continue
        label = str(terminal.get("expected_marking", "")).strip().lower()
        detected = str(terminal.get("detected_marking", "")).strip().lower()
        if label not in SUPPORTED_LABELS or detected != label:
            continue
        if terminal.get("marking_evaluated") is not True:
            continue
        if terminal.get("marking_pass") is not True:
            continue
        diagnostics = dict(terminal.get("diagnostic_image_paths") or {})
        source_text = str(diagnostics.get("terminal_top") or "")
        if not source_text and allow_legacy_marking_crops:
            source_text = str(terminal.get("marking_crop_path", "") or "")
        if not source_text:
            # Older evidence can include the red ring or molded case symbol in
            # marking_crop_path.  Do not feed that context to an ML model unless
            # an engineer explicitly opts into legacy export.
            continue
        source = _resolve_evidence_path(source_text, manifest_path)
        if not source.is_file():
            continue
        yield result, recipe, terminal, validation_mode


def export_marking_dataset(
    data_directory: Path,
    output_directory: Path,
    *,
    include_production_passes: bool = False,
    allow_legacy_marking_crops: bool = False,
    clean: bool = False,
) -> dict[str, Any]:
    """Export known-good isolated terminal tops into a class-folder dataset.

    Only cycle manifests with an overall PASS and a per-terminal marking PASS are
    accepted. Labels come from the immutable recipe expectation, not from a
    low-confidence model guess. By default only explicit ``terminal_top``
    evidence is eligible so the red ring and molded case symbols cannot leak
    into training. Duplicate image bytes are copied once per class.
    """

    data_directory = Path(data_directory)
    output_directory = Path(output_directory)
    if clean and output_directory.exists():
        shutil.rmtree(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    for label in sorted(SUPPORTED_LABELS):
        (output_directory / label).mkdir(parents=True, exist_ok=True)

    records: list[DatasetExportRecord] = []
    seen_by_label: set[tuple[str, str]] = set()
    skipped_invalid_json = 0
    manifests = discover_manifests(
        data_directory,
        include_production_passes=include_production_passes,
    )

    for manifest_path in manifests:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            skipped_invalid_json += 1
            continue
        for result, recipe, terminal, validation_mode in _eligible_terminal_records(
            manifest_path,
            payload,
            include_production_passes=include_production_passes,
            allow_legacy_marking_crops=allow_legacy_marking_crops,
        ):
            diagnostics = dict(terminal.get("diagnostic_image_paths") or {})
            source_text = str(diagnostics.get("terminal_top") or "")
            if not source_text and allow_legacy_marking_crops:
                source_text = str(terminal.get("marking_crop_path", "") or "")
            source = _resolve_evidence_path(source_text, manifest_path)
            label = str(terminal.get("expected_marking", "")).strip().lower()
            digest = _sha256(source)
            dedupe_key = (label, digest)
            if dedupe_key in seen_by_label:
                continue
            seen_by_label.add(dedupe_key)
            suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"} else ".png"
            recipe_id = str(recipe.get("recipe_id", result.get("recipe_id", "")))
            revision = int(recipe.get("revision", 0) or 0)
            terminal_key = str(terminal.get("terminal_key", "terminal"))
            destination_name = (
                f"{recipe_id[:12] or 'recipe'}_r{revision:04d}_"
                f"{terminal_key}_{digest[:16]}{suffix}"
            )
            destination = output_directory / label / destination_name
            shutil.copy2(source, destination)
            records.append(
                DatasetExportRecord(
                    label=label,
                    destination_path=str(destination.resolve()),
                    source_path=str(source.resolve()),
                    sha256=digest,
                    recipe_id=recipe_id,
                    recipe_name=str(recipe.get("name", result.get("recipe_name", ""))),
                    recipe_revision=revision,
                    inspection_id=str(result.get("inspection_id", "")),
                    cycle_id=str(result.get("cycle_id", "")),
                    captured_at_utc=str(result.get("captured_at_utc", result.get("timestamp_utc", ""))),
                    terminal_key=terminal_key,
                    terminal_role=str(terminal.get("role", "")),
                    expected_marking=label,
                    detected_marking=str(terminal.get("detected_marking", "")),
                    marking_confidence=float(terminal.get("marking_confidence", 0.0) or 0.0),
                    classifier_status=str(terminal.get("classification_status", "")),
                    validation_mode=validation_mode,
                )
            )

    fieldnames = list(DatasetExportRecord.__dataclass_fields__.keys())
    with (output_directory / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(record.to_dict() for record in records)
    with (output_directory / "manifest.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")

    counts = {
        label: sum(1 for record in records if record.label == label)
        for label in sorted(SUPPORTED_LABELS)
    }
    summary = {
        "schema_version": 1,
        "data_directory": str(data_directory.resolve()),
        "output_directory": str(output_directory.resolve()),
        "include_production_passes": bool(include_production_passes),
        "allow_legacy_marking_crops": bool(allow_legacy_marking_crops),
        "manifest_count": len(manifests),
        "record_count": len(records),
        "counts": counts,
        "invalid_manifest_count": skipped_invalid_json,
    }
    (output_directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def prepare_classification_dataset(
    exported_directory: Path,
    output_directory: Path,
    *,
    manual_directory: Path | None = None,
    validation_fraction: float = 0.15,
    test_fraction: float = 0.15,
    clean: bool = False,
) -> dict[str, Any]:
    """Build train/val/test class folders for an image-classification trainer.

    Automatic evidence records are split by inspection cycle so both terminals
    from one physical presentation stay in the same partition.  Optional manual
    examples are split by file hash.  This is a data
    preparation utility, not an approval gate; the resulting model still needs a
    held-out challenge set and recipe validation before production use.
    """

    exported_directory = Path(exported_directory)
    output_directory = Path(output_directory)
    manual_directory = Path(manual_directory) if manual_directory else None
    validation_fraction = min(0.40, max(0.05, float(validation_fraction)))
    test_fraction = min(0.40, max(0.05, float(test_fraction)))
    if validation_fraction + test_fraction >= 0.80:
        raise ValueError("validation_fraction + test_fraction must be below 0.80")

    if clean and output_directory.exists():
        shutil.rmtree(output_directory)
    for split in ("train", "val", "test"):
        for label in sorted(SUPPORTED_LABELS):
            (output_directory / split / label).mkdir(parents=True, exist_ok=True)

    def partition(group_key: str) -> str:
        value = int(hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:12], 16)
        ratio = value / float(0xFFFFFFFFFFFF)
        if ratio < test_fraction:
            return "test"
        if ratio < test_fraction + validation_fraction:
            return "val"
        return "train"

    prepared: list[dict[str, Any]] = []
    manifest_path = exported_directory / "manifest.jsonl"
    if manifest_path.is_file():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            label = str(record.get("label", "")).strip().lower()
            if label not in SUPPORTED_LABELS:
                continue
            source = Path(str(record.get("destination_path", "") or ""))
            if not source.is_file():
                source = exported_directory / label / source.name
            if not source.is_file():
                continue
            group = str(record.get("cycle_id", "") or record.get("inspection_id", "") or source.stem)
            split = partition("evidence:" + group)
            destination = output_directory / split / label / source.name
            shutil.copy2(source, destination)
            prepared.append(
                {
                    "split": split,
                    "label": label,
                    "source": str(source.resolve()),
                    "destination": str(destination.resolve()),
                    "group": group,
                    "source_kind": "evidence",
                }
            )

    if manual_directory and manual_directory.is_dir():
        for label in sorted(SUPPORTED_LABELS):
            source_dir = manual_directory / label
            if not source_dir.is_dir():
                continue
            for source in sorted(source_dir.iterdir()):
                if not source.is_file() or source.suffix.lower() not in {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".bmp",
                    ".webp",
                }:
                    continue
                digest = _sha256(source)
                split = partition("manual:" + digest)
                destination = output_directory / split / label / f"manual_{digest[:16]}{source.suffix.lower()}"
                if not destination.exists():
                    shutil.copy2(source, destination)
                prepared.append(
                    {
                        "split": split,
                        "label": label,
                        "source": str(source.resolve()),
                        "destination": str(destination.resolve()),
                        "group": digest,
                        "source_kind": "manual",
                    }
                )

    counts = {
        split: {
            label: sum(
                1
                for item in prepared
                if item["split"] == split and item["label"] == label
            )
            for label in sorted(SUPPORTED_LABELS)
        }
        for split in ("train", "val", "test")
    }
    summary = {
        "schema_version": 1,
        "exported_directory": str(exported_directory.resolve()),
        "output_directory": str(output_directory.resolve()),
        "manual_directory": str(manual_directory.resolve()) if manual_directory else "",
        "validation_fraction": validation_fraction,
        "test_fraction": test_fraction,
        "record_count": len(prepared),
        "counts": counts,
    }
    (output_directory / "dataset_manifest.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in prepared),
        encoding="utf-8",
    )
    (output_directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary
