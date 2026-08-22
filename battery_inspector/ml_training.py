from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import cv2

from battery_inspector.models import NormalizedRect, ReferenceCapture
from battery_inspector.roi_geometry import (
    CIRCLE_ROI_SHAPE,
    TAUGHT_CIRCLE_CROP_CONTRACT,
    ml_input_crop,
    normalize_roi_shape,
)
from battery_inspector.services.ml import REQUIRED_POLARITY_CLASSES, OnnxPolarityModel, sha256_file

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
TRAINING_LABELS = tuple(REQUIRED_POLARITY_CLASSES)
REVIEW_LABELS = TRAINING_LABELS


class MlTrainingError(RuntimeError):
    """Raised when guided training cannot continue safely."""


@dataclass(slots=True)
class MlTrainingSample:
    sample_id: str
    label: str
    image_path: str
    source_image_path: str
    source_capture_id: str
    captured_at_utc: str
    roi: dict[str, float]
    sha256: str
    width_px: int
    height_px: int
    source_frame_id: str = ""
    camera_backend: str = ""
    camera_description: str = ""
    crop_quality: dict[str, Any] | None = None
    collection_tag: str = ""
    batch_index: int = 0
    roi_key: str = ""
    roi_shape: str = CIRCLE_ROI_SHAPE
    crop_contract: str = TAUGHT_CIRCLE_CROP_CONTRACT

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MlTrainingSample":
        return cls(
            sample_id=str(payload.get("sample_id", "")),
            label=str(payload.get("label", "")).lower(),
            image_path=str(payload.get("image_path", "")),
            source_image_path=str(payload.get("source_image_path", "")),
            source_capture_id=str(payload.get("source_capture_id", "")),
            captured_at_utc=str(payload.get("captured_at_utc", "")),
            roi=dict(payload.get("roi") or {}),
            sha256=str(payload.get("sha256", "")),
            width_px=int(payload.get("width_px", 0) or 0),
            height_px=int(payload.get("height_px", 0) or 0),
            source_frame_id=str(payload.get("source_frame_id", "")),
            camera_backend=str(payload.get("camera_backend", "")),
            camera_description=str(payload.get("camera_description", "")),
            crop_quality=dict(payload.get("crop_quality") or {}),
            collection_tag=str(payload.get("collection_tag", "")),
            batch_index=int(payload.get("batch_index", 0) or 0),
            roi_key=str(payload.get("roi_key", "")),
            roi_shape=normalize_roi_shape(payload.get("roi_shape", CIRCLE_ROI_SHAPE)),
            crop_contract=str(payload.get("crop_contract", TAUGHT_CIRCLE_CROP_CONTRACT) or TAUGHT_CIRCLE_CROP_CONTRACT),
        )


@dataclass(slots=True)
class MlTrainingParameters:
    base_model: str = "yolo11n-cls.pt"
    epochs: int = 80
    image_size: int = 224
    batch: int = 32
    device: str = "cpu"
    model_id: str = "polarity-terminal-top-yolo"
    model_version: str = ""
    minimum_confidence: float = 0.90
    minimum_margin: float = 0.15
    # New circle-contract models are evaluated exactly as they are seen by the
    # operator. Full-rotation augmentation is used during training, so quadrant
    # test-time averaging is disabled by default to avoid introducing a second
    # inference convention between validation and production.
    tta_quadrants: bool = False

    def normalized(self) -> "MlTrainingParameters":
        version = self.model_version.strip() or datetime.now(timezone.utc).strftime("%Y.%m.%d.%H%M")
        return MlTrainingParameters(
            base_model=self.base_model.strip() or "yolo11n-cls.pt",
            epochs=max(1, int(self.epochs)),
            image_size=max(96, int(self.image_size)),
            batch=max(1, int(self.batch)),
            device=self.device.strip() or "cpu",
            model_id=self.model_id.strip() or "polarity-terminal-top-yolo",
            model_version=version,
            minimum_confidence=min(0.999, max(0.0, float(self.minimum_confidence))),
            minimum_margin=min(0.99, max(0.0, float(self.minimum_margin))),
            tta_quadrants=bool(self.tta_quadrants),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalized())


ProgressCallback = Callable[[dict[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quality(image) -> dict[str, float | str]:
    if image is None or image.size == 0:
        return {"status": "POOR", "mean_level": 0.0, "sharpness": 0.0}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    mean_level = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    clipped = float((gray >= 250).mean())
    dark = float((gray <= 5).mean())
    status = "GOOD"
    if mean_level < 15 or mean_level > 245 or sharpness < 8 or clipped > 0.40 or dark > 0.40:
        status = "POOR"
    return {
        "status": status,
        "mean_level": mean_level,
        "sharpness": sharpness,
        "clipped_fraction": clipped,
        "dark_fraction": dark,
    }


class MlTrainingStore:
    """Persistent, camera-first sample store used by the guided HMI wizard.

    Samples are saved directly from a freshly captured full-resolution frame and
    an operator-confirmed normalized ROI. The training workflow never requires
    inspection evidence folders.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.samples_root = self.root / "samples"
        self.staging_root = self.root / "staging"
        self.datasets_root = self.root / "datasets"
        self.runs_root = self.root / "runs"
        self.manifest_path = self.root / "samples.jsonl"
        # The current guided contract uses four physical image classes. Existing
        # PLUS/MINUS/BLANK samples remain valid; technicians add INVALID_MARKING
        # examples without resetting the persistent store.
        for label in REVIEW_LABELS:
            (self.samples_root / label).mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.datasets_root.mkdir(parents=True, exist_ok=True)
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def records(self) -> list[MlTrainingSample]:
        if not self.manifest_path.is_file():
            return []
        records: list[MlTrainingSample] = []
        for line in self.manifest_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = MlTrainingSample.from_dict(json.loads(line))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if record.label not in REVIEW_LABELS:
                continue
            if not Path(record.image_path).is_file():
                continue
            records.append(record)
        return records

    def counts(self) -> dict[str, int]:
        records = self.records()
        return {label: sum(1 for item in records if item.label == label) for label in TRAINING_LABELS}

    def latest(self, label: str, limit: int = 1) -> list[MlTrainingSample]:
        label = label.lower().strip()
        items = [item for item in self.records() if item.label == label]
        return items[-max(1, int(limit)) :][::-1]

    def _prepare_crop(
        self,
        image,
        roi: NormalizedRect,
        *,
        roi_shape: str = CIRCLE_ROI_SHAPE,
    ) -> tuple[NormalizedRect, bytes, str, int, int, dict[str, Any], str]:
        rect = roi.clamped()
        if rect.width < 0.01 or rect.height < 0.01:
            raise MlTrainingError("Training ROI is too small. Draw a circle around the metal terminal top.")
        height, width = image.shape[:2]
        normalized_shape = normalize_roi_shape(roi_shape)
        crop, rect, shape_metrics, crop_contract = ml_input_crop(
            image,
            rect,
            normalized_shape,
        )
        if crop.shape[0] < 48 or crop.shape[1] < 48:
            raise MlTrainingError(
                "Training ROI is too small in pixels. Keep at least 48 x 48 pixels around the metal top."
            )
        encoded_ok, encoded = cv2.imencode(".png", crop)
        if not encoded_ok:
            raise MlTrainingError("Could not encode the terminal-top training crop")
        payload = encoded.tobytes()
        digest = hashlib.sha256(payload).hexdigest()
        crop_quality = _quality(crop)
        crop_quality.update(shape_metrics)
        return (
            rect,
            payload,
            digest,
            int(crop.shape[1]),
            int(crop.shape[0]),
            crop_quality,
            crop_contract,
        )

    def save_samples(
        self,
        capture: ReferenceCapture,
        items: list[tuple[str, NormalizedRect, str]],
        *,
        collection_tag: str = "",
        roi_shape: str = CIRCLE_ROI_SHAPE,
    ) -> list[tuple[MlTrainingSample, bool]]:
        """Save multiple terminal-top crops from one fresh frame as one logical batch.

        ``items`` contains ``(roi_key, roi, label)`` tuples. All crops are
        validated before any new manifest records are appended, so a bad ROI
        cannot leave a partially saved multi-terminal batch. Duplicate bytes in
        the same class reuse the existing record and are reported as duplicates.
        """

        if not items:
            raise MlTrainingError("Add at least one terminal-top ROI before saving the capture")
        source = Path(capture.path)
        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image is None:
            raise MlTrainingError(f"Captured training image could not be opened: {source}")

        existing = self.records()
        existing_by_class_hash = {(record.label, record.sha256): record for record in existing}
        prepared: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for batch_index, (roi_key, roi, label) in enumerate(items, start=1):
            label = str(label).strip().lower()
            if label not in TRAINING_LABELS:
                raise MlTrainingError(f"Unsupported ML class: {label}")
            roi_key = str(roi_key or f"roi_{batch_index}").strip()
            if roi_key in seen_keys:
                raise MlTrainingError(f"Duplicate ROI key in capture batch: {roi_key}")
            seen_keys.add(roi_key)
            (
                rect,
                encoded,
                digest,
                width_px,
                height_px,
                crop_quality,
                crop_contract,
            ) = self._prepare_crop(image, roi, roi_shape=roi_shape)
            duplicate = existing_by_class_hash.get((label, digest))
            prepared.append(
                {
                    "batch_index": batch_index,
                    "roi_key": roi_key,
                    "rect": rect,
                    "label": label,
                    "encoded": encoded,
                    "digest": digest,
                    "width_px": width_px,
                    "height_px": height_px,
                    "crop_quality": crop_quality,
                    "roi_shape": normalize_roi_shape(roi_shape),
                    "crop_contract": crop_contract,
                    "duplicate": duplicate,
                }
            )

        new_records: list[MlTrainingSample] = []
        results: list[tuple[MlTrainingSample, bool]] = []
        written_paths: list[Path] = []
        created_by_class_hash: dict[tuple[str, str], MlTrainingSample] = {}
        tag = str(collection_tag or "").strip()
        try:
            for item in prepared:
                duplicate = item["duplicate"]
                if duplicate is not None:
                    results.append((duplicate, True))
                    continue
                same_batch = created_by_class_hash.get((item["label"], item["digest"]))
                if same_batch is not None:
                    results.append((same_batch, True))
                    continue
                sample_id = str(uuid4())
                label = item["label"]
                digest = item["digest"]
                destination = self.samples_root / label / f"{label}_{sample_id[:8]}_{digest[:16]}.png"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(item["encoded"])
                written_paths.append(destination)
                record = MlTrainingSample(
                    sample_id=sample_id,
                    label=label,
                    image_path=str(destination.resolve()),
                    source_image_path=str(source.resolve()),
                    source_capture_id=capture.capture_id,
                    captured_at_utc=capture.captured_at_utc or _utc_now(),
                    roi=item["rect"].to_dict(),
                    sha256=digest,
                    width_px=item["width_px"],
                    height_px=item["height_px"],
                    source_frame_id=capture.frame_id,
                    camera_backend=capture.camera_backend,
                    camera_description=capture.camera_description,
                    crop_quality=item["crop_quality"],
                    collection_tag=tag,
                    batch_index=item["batch_index"],
                    roi_key=item["roi_key"],
                    roi_shape=item["roi_shape"],
                    crop_contract=item["crop_contract"],
                )
                new_records.append(record)
                created_by_class_hash[(label, digest)] = record
                results.append((record, False))
            if new_records:
                self.root.mkdir(parents=True, exist_ok=True)
                with self.manifest_path.open("a", encoding="utf-8") as handle:
                    for record in new_records:
                        handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
        except Exception:
            for path in written_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        return results

    def save_sample(
        self,
        capture: ReferenceCapture,
        roi: NormalizedRect,
        label: str,
        *,
        collection_tag: str = "",
        roi_key: str = "ml_top",
        roi_shape: str = CIRCLE_ROI_SHAPE,
    ) -> MlTrainingSample:
        return self.save_samples(
            capture,
            [(roi_key, roi, label)],
            collection_tag=collection_tag,
            roi_shape=roi_shape,
        )[0][0]

    def _write_records(self, records: list[MlTrainingSample]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            "".join(json.dumps(item.to_dict(), sort_keys=True) + "\n" for item in records),
            encoding="utf-8",
        )

    def remove_sample(self, sample_id: str) -> bool:
        records = self.records()
        target = next((item for item in records if item.sample_id == sample_id), None)
        if target is None:
            return False
        try:
            Path(target.image_path).unlink(missing_ok=True)
        except OSError:
            pass
        remaining = [item for item in records if item.sample_id != sample_id]
        self._write_records(remaining)
        return True

    def relabel_sample(self, sample_id: str, label: str) -> dict[str, Any]:
        """Correct one stored label without losing the original capture metadata.

        If the exact same crop already exists under the requested class, the
        duplicate record is removed instead of creating two identical samples.
        """
        label = str(label or "").strip().lower()
        if label not in TRAINING_LABELS:
            raise MlTrainingError(f"Unsupported ML class: {label}")
        records = self.records()
        target = next((item for item in records if item.sample_id == sample_id), None)
        if target is None:
            raise MlTrainingError(f"Training sample was not found: {sample_id}")
        if target.label == label:
            return {"sample": target.to_dict(), "merged_duplicate": False}

        duplicate = next(
            (
                item
                for item in records
                if item.sample_id != target.sample_id
                and item.label == label
                and item.sha256 == target.sha256
            ),
            None,
        )
        if duplicate is not None:
            try:
                Path(target.image_path).unlink(missing_ok=True)
            except OSError:
                pass
            self._write_records([item for item in records if item.sample_id != target.sample_id])
            return {"sample": duplicate.to_dict(), "merged_duplicate": True}

        old_path = Path(target.image_path)
        destination = self.samples_root / label / (
            f"{label}_{target.sample_id[:8]}_{target.sha256[:16]}{old_path.suffix or '.png'}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if old_path.is_file():
            try:
                old_path.replace(destination)
            except OSError:
                shutil.copy2(old_path, destination)
                old_path.unlink(missing_ok=True)
        target.label = label
        target.image_path = str(destination.resolve())
        self._write_records(records)
        return {"sample": target.to_dict(), "merged_duplicate": False}

    def sample_catalog(self) -> list[dict[str, Any]]:
        """Return newest-first persistent sample metadata for the HMI browser."""
        return [item.to_dict() for item in reversed(self.records())]

    @staticmethod
    def _group_sort_key(label: str, group: str) -> str:
        return hashlib.sha256(f"{label}:{group}".encode("utf-8")).hexdigest()

    def prepare_dataset(
        self,
        output_directory: Path | None = None,
        *,
        validation_fraction: float = 0.15,
        test_fraction: float = 0.15,
        clean: bool = True,
    ) -> dict[str, Any]:
        """Prepare a leakage-safe grouped dataset without arbitrary collection-count gates.

        Collection targets are advisory. Every crop from one camera frame stays
        in the same split. The splitter always protects training-class coverage
        first, then moves as many independent capture groups as practical into
        validation and test. Small datasets are still prepared; the returned
        summary reports structural limitations instead of rejecting the dataset
        because an advisory target has not been reached.
        """
        records = [item for item in self.records() if item.label in TRAINING_LABELS]
        incompatible = [
            item
            for item in records
            if normalize_roi_shape(item.roi_shape) != CIRCLE_ROI_SHAPE
            or str(item.crop_contract or TAUGHT_CIRCLE_CROP_CONTRACT)
            != TAUGHT_CIRCLE_CROP_CONTRACT
        ]
        if incompatible:
            raise MlTrainingError(
                "Training store contains incompatible pre-v0.17 samples. "
                "The clean baseline must contain circle ROIs only; reset or remove those samples before preparing the dataset."
            )
        validation_fraction = min(0.35, max(0.05, float(validation_fraction)))
        test_fraction = min(0.35, max(0.05, float(test_fraction)))
        if validation_fraction + test_fraction >= 0.70:
            raise MlTrainingError("Validation + test fractions must be below 70%")
        if output_directory is None:
            output_directory = self.datasets_root / "current"
        output_directory = Path(output_directory)
        if clean and output_directory.exists():
            shutil.rmtree(output_directory)
        for split in ("train", "val", "test"):
            for label in TRAINING_LABELS:
                (output_directory / split / label).mkdir(parents=True, exist_ok=True)

        groups: dict[str, list[MlTrainingSample]] = {}
        for record in records:
            group = record.source_capture_id or record.sample_id
            groups.setdefault(group, []).append(record)

        labels_by_group: dict[str, set[str]] = {
            group: {item.label for item in items} for group, items in groups.items()
        }
        groups_by_label: dict[str, list[str]] = {
            label: [group for group, labels in labels_by_group.items() if label in labels]
            for label in TRAINING_LABELS
        }

        # Start with every capture group in training so no class can be lost from
        # training. Then move groups to held-out splits only when at least one
        # independent training group remains for every class represented by that
        # group. This permits small candidate runs without pretending that sparse
        # validation/test coverage is statistically sufficient.
        assignment: dict[str, str] = {group: "train" for group in groups}

        def train_groups() -> set[str]:
            return {group for group, split in assignment.items() if split == "train"}

        def train_label_group_count(label: str) -> int:
            return sum(1 for group in train_groups() if label in labels_by_group[group])

        def can_move_from_train(group: str, *, minimum_train_groups: int = 1) -> bool:
            current_train = train_groups()
            if group not in current_train:
                return False
            if len(current_train) <= minimum_train_groups:
                return False
            for label in labels_by_group[group]:
                if train_label_group_count(label) <= 1:
                    return False
            return True

        total_groups = len(groups)
        desired_test = max(1, int(round(total_groups * test_fraction))) if total_groups >= 3 else 0
        desired_val = max(1, int(round(total_groups * validation_fraction))) if total_groups >= 2 else 0

        def move_groups(split: str, desired: int, *, reserve_train_groups: int) -> None:
            for _ in range(max(0, desired)):
                current_coverage = {
                    label: sum(
                        1
                        for group, assigned in assignment.items()
                        if assigned == split and label in labels_by_group[group]
                    )
                    for label in TRAINING_LABELS
                }
                candidates: list[tuple[int, int, str, str]] = []
                for group in sorted(train_groups()):
                    if not can_move_from_train(group, minimum_train_groups=reserve_train_groups):
                        continue
                    new_coverage = sum(1 for label in labels_by_group[group] if current_coverage[label] == 0)
                    total_coverage = len(labels_by_group[group])
                    candidates.append(
                        (-new_coverage, -total_coverage, self._group_sort_key(split, group), group)
                    )
                if not candidates:
                    return
                candidates.sort()
                assignment[candidates[0][3]] = split

        # Preserve enough train groups to make a validation move after the test
        # split when practical. If the dataset is tiny, the test split simply
        # remains smaller/empty and the HMI reports that as advisory evidence.
        reserve_for_test = 2 if desired_val else 1
        move_groups("test", desired_test, reserve_train_groups=reserve_for_test)
        move_groups("val", desired_val, reserve_train_groups=1)

        assignments: list[dict[str, Any]] = []
        for group, items in groups.items():
            split = assignment[group]
            for item in items:
                source = Path(item.image_path)
                destination = output_directory / split / item.label / source.name
                shutil.copy2(source, destination)
                assignments.append(
                    {
                        "sample_id": item.sample_id,
                        "label": item.label,
                        "capture_group": group,
                        "split": split,
                        "source": str(source.resolve()),
                        "destination": str(destination.resolve()),
                        "roi_shape": item.roi_shape,
                        "crop_contract": item.crop_contract,
                    }
                )

        counts = {
            split: {
                label: sum(1 for item in assignments if item["split"] == split and item["label"] == label)
                for label in TRAINING_LABELS
            }
            for split in ("train", "val", "test")
        }
        train_missing = [label for label in TRAINING_LABELS if counts["train"][label] <= 0]
        val_total = sum(counts["val"].values())
        test_total = sum(counts["test"].values())
        test_missing = [label for label in TRAINING_LABELS if counts["test"][label] <= 0]
        training_issues: list[str] = []
        if train_missing:
            training_issues.append(
                "training split has no " + ", ".join(label.upper() for label in train_missing)
            )
        if val_total <= 0:
            training_issues.append(
                "no independent validation group could be reserved; capture at least one additional varied frame"
            )

        crop_contract_counts: dict[str, int] = {}
        for item in assignments:
            contract = str(item.get("crop_contract", TAUGHT_CIRCLE_CROP_CONTRACT) or TAUGHT_CIRCLE_CROP_CONTRACT)
            crop_contract_counts[contract] = crop_contract_counts.get(contract, 0) + 1
        summary = {
            "schema_version": 4,
            "prepared_at_utc": _utc_now(),
            "output_directory": str(output_directory.resolve()),
            "validation_fraction": validation_fraction,
            "test_fraction": test_fraction,
            "record_count": len(assignments),
            "capture_group_count": {
                label: len(groups_by_label[label]) for label in TRAINING_LABELS
            },
            "total_capture_groups": len(groups),
            "counts": counts,
            "training_ready": not training_issues,
            "training_issues": training_issues,
            "held_out_available": test_total > 0,
            "held_out_class_coverage_complete": not test_missing and test_total > 0,
            "held_out_classes_missing": test_missing,
            "collection_targets_are_advisory": True,
            "crop_contract_counts": dict(sorted(crop_contract_counts.items())),
            "preferred_crop_contract": TAUGHT_CIRCLE_CROP_CONTRACT,
        }
        (output_directory / "dataset_manifest.jsonl").write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in assignments),
            encoding="utf-8",
        )
        (output_directory / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return summary

    def dataset_readiness(self, *, engineering_minimum_per_class: int = 8) -> dict[str, Any]:
        """Return collection coverage; collection targets never gate the wizard.

        ``engineering_minimum_per_class`` is retained only for API compatibility
        with older callers and is intentionally ignored.
        """
        records = [item for item in self.records() if item.label in TRAINING_LABELS]
        eligible_records = [
            item
            for item in records
            if normalize_roi_shape(item.roi_shape) == CIRCLE_ROI_SHAPE
            and str(item.crop_contract or TAUGHT_CIRCLE_CROP_CONTRACT)
            == TAUGHT_CIRCLE_CROP_CONTRACT
        ]
        counts = {
            label: sum(1 for item in eligible_records if item.label == label)
            for label in TRAINING_LABELS
        }
        independent_captures = {
            label: len(
                {
                    item.source_capture_id or item.sample_id
                    for item in eligible_records
                    if item.label == label
                }
            )
            for label in TRAINING_LABELS
        }
        recommended = {
            "plus": 100,
            "minus": 100,
            "blank": 100,
            "invalid_marking": 100,
        }
        collection_tags: dict[str, int] = {}
        for item in eligible_records:
            tag = str(item.collection_tag or "").strip()
            if tag:
                collection_tags[tag] = collection_tags.get(tag, 0) + 1
        capture_groups = {
            item.source_capture_id or item.sample_id for item in eligible_records
        }
        crop_contracts: dict[str, int] = {}
        for item in records:
            contract = str(item.crop_contract or TAUGHT_CIRCLE_CROP_CONTRACT)
            crop_contracts[contract] = crop_contracts.get(contract, 0) + 1
        classes_without_samples = [
            label for label in TRAINING_LABELS if counts[label] <= 0
        ]
        classes_below_target = [
            label
            for label in TRAINING_LABELS
            if independent_captures[label] < recommended[label]
        ]
        return {
            "counts": counts,
            "independent_captures": independent_captures,
            "total_samples": len(eligible_records),
            "total_stored_samples": len(records),
            "eligible_circle_samples": len(eligible_records),
            "incompatible_sample_count": len(records) - len(eligible_records),
            "total_capture_groups": len(capture_groups),
            "collection_tags": dict(sorted(collection_tags.items())),
            "collection_tag_count": len(collection_tags),
            "recommended": recommended,
            "classes_without_samples": classes_without_samples,
            "class_coverage_complete": not classes_without_samples,
            "classes_below_target": classes_below_target,
            "production_target_met": not classes_below_target,
            "collection_targets_are_advisory": True,
            "crop_contract_counts": dict(sorted(crop_contracts.items())),
            "preferred_crop_contract": TAUGHT_CIRCLE_CROP_CONTRACT,
            # Deprecated compatibility keys. They no longer represent a gate.
            "engineering_minimum_per_class": 0,
            "minimum_met": True,
            "classes_below_minimum": [],
        }


    def latest_training_result(self) -> dict[str, Any] | None:
        """Recover the newest exported candidate after an HMI restart.

        A failed post-training runtime check must not force another expensive
        training run.  The ONNX model and manifest are sufficient to recover a
        candidate; held-out evaluation is loaded when available.
        """
        if not self.runs_root.is_dir():
            return None
        candidates: list[tuple[float, Path]] = []
        for run_dir in self.runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            model_path = run_dir / "polarity_classifier.onnx"
            manifest_path = run_dir / "polarity_classifier.json"
            if not model_path.is_file() or not manifest_path.is_file():
                continue
            try:
                stamp = max(model_path.stat().st_mtime, manifest_path.stat().st_mtime)
            except OSError:
                continue
            candidates.append((stamp, run_dir))
        if not candidates:
            return None
        _stamp, run_dir = max(candidates, key=lambda item: item[0])
        model_path = run_dir / "polarity_classifier.onnx"
        manifest_path = run_dir / "polarity_classifier.json"
        evaluation_path = run_dir / "evaluation.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        evaluation: dict[str, Any] = {}
        if evaluation_path.is_file():
            try:
                evaluation = dict(json.loads(evaluation_path.read_text(encoding="utf-8")) or {})
            except (OSError, json.JSONDecodeError, TypeError):
                evaluation = {}
        if not evaluation:
            evaluation = {
                "held_out_available": False,
                "total_images": 0,
                "accepted_images": 0,
                "acceptance_rate": 0.0,
                "accuracy_with_abstentions": 0.0,
                "accepted_accuracy": 0.0,
                "per_class": {
                    label: {
                        "count": 0,
                        "correct": 0,
                        "recall_with_abstentions": 0.0,
                        "low_confidence": 0,
                    }
                    for label in TRAINING_LABELS
                },
                "note": (
                    "Candidate recovered from exported ONNX artifacts. Held-out evaluation "
                    "was not completed in the prior session; installation for recipe "
                    "validation remains possible after runtime verification."
                ),
            }
        metadata = dict(manifest.get("metadata") or {})
        return {
            "run_id": run_dir.name,
            "run_directory": str(run_dir.resolve()),
            "model_path": str(model_path.resolve()),
            "manifest_path": str(manifest_path.resolve()),
            "evaluation_path": str(evaluation_path.resolve()) if evaluation_path.is_file() else "",
            "model_sha256": str(manifest.get("model_sha256", "")),
            "parameters": dict(metadata.get("training_parameters") or {}),
            "counts": dict(metadata.get("counts") or {}),
            "evaluation": evaluation,
            "recovered": True,
            "evaluation_recovered": bool(evaluation_path.is_file()),
        }


def training_environment() -> dict[str, Any]:
    result: dict[str, Any] = {
        "ready": False,
        "ultralytics": False,
        "torch": False,
        "onnx": False,
        "onnxruntime": False,
        "cuda_available": False,
        "cuda_device_count": 0,
        "cuda_device_names": [],
        "nvidia_hardware_names": [],
        "issues": [],
        "warnings": [],
    }
    try:
        import ultralytics  # type: ignore

        result["ultralytics"] = True
        result["ultralytics_version"] = str(getattr(ultralytics, "__version__", ""))
    except Exception as exc:  # noqa: BLE001
        result["issues"].append(f"Ultralytics not available: {exc}")
    try:
        import onnx  # type: ignore

        result["onnx"] = True
        result["onnx_version"] = str(getattr(onnx, "__version__", ""))
    except Exception as exc:  # noqa: BLE001
        result["issues"].append(f"ONNX export package not available: {exc}")
    try:
        import onnxruntime as ort  # type: ignore

        result["onnxruntime"] = True
        result["onnxruntime_version"] = str(getattr(ort, "__version__", ""))
        result["onnxruntime_providers"] = list(ort.get_available_providers())
    except Exception as exc:  # noqa: BLE001
        result["issues"].append(f"ONNX Runtime not available: {exc}")
    try:
        import subprocess

        probe = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if probe.returncode == 0:
            result["nvidia_hardware_names"] = [
                line.strip() for line in probe.stdout.splitlines() if line.strip()
            ]
    except Exception:  # noqa: S110 - no nvidia-smi is a normal CPU-only workstation, not an error
        pass
    try:
        import torch  # type: ignore

        result["torch"] = True
        result["torch_version"] = str(getattr(torch, "__version__", ""))
        cuda_available = bool(torch.cuda.is_available())
        result["cuda_available"] = cuda_available
        if cuda_available:
            count = int(torch.cuda.device_count())
            result["cuda_device_count"] = count
            result["cuda_device_names"] = [str(torch.cuda.get_device_name(index)) for index in range(count)]
        elif result["nvidia_hardware_names"]:
            result["warnings"].append(
                "NVIDIA GPU hardware is present, but this PyTorch build does not expose CUDA. "
                "Training will use CPU until a CUDA-enabled PyTorch wheel is installed."
            )
    except Exception as exc:  # noqa: BLE001
        result["issues"].append(f"PyTorch not available: {exc}")
    result["ready"] = bool(
        result["ultralytics"]
        and result["torch"]
        and result["onnx"]
        and result["onnxruntime"]
    )
    return result


def _class_counts(dataset: Path, split: str) -> dict[str, int]:
    root = dataset / split
    return {
        label: len(
            [
                path
                for path in (root / label).glob("*")
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            ]
        )
        for label in TRAINING_LABELS
    }


def evaluate_onnx_model(
    model_path: Path,
    manifest_path: Path,
    test_directory: Path,
    *,
    minimum_confidence: float = 0.90,
    minimum_margin: float = 0.15,
    tta_quadrants: bool = False,
) -> dict[str, Any]:
    model = OnnxPolarityModel(model_path, manifest_path)
    info = model.info(require_runtime=True)
    if not info.get("ready"):
        raise MlTrainingError("Trained ONNX model failed runtime verification: " + "; ".join(info.get("issues", [])))
    labels = list(TRAINING_LABELS)
    confusion = {actual: {predicted: 0 for predicted in labels + ["low_confidence"]} for actual in labels}
    total_count = 0
    accepted_count = 0
    correct_accepted = 0
    per_class_total = {label: 0 for label in labels}
    unreadable_files: list[str] = []
    for actual in labels:
        folder = test_directory / actual
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                unreadable_files.append(str(path))
                continue
            inference = model.infer(image, tta_quadrants=tta_quadrants)
            total_count += 1
            per_class_total[actual] += 1
            if (
                inference.confidence < minimum_confidence
                or inference.margin < minimum_margin
                or inference.top_label not in labels
            ):
                # Confidence failure is a fail-closed no-decision/abstention.
                # It remains distinct from a confident INVALID_MARKING result.
                predicted = "low_confidence"
            else:
                predicted = inference.top_label
                accepted_count += 1
                if predicted == actual:
                    correct_accepted += 1
            confusion[actual][predicted] += 1
    if total_count <= 0:
        raise MlTrainingError("Held-out test set contains no readable images")
    per_class: dict[str, dict[str, float | int]] = {}
    correct_total = 0
    for actual in labels:
        total = per_class_total[actual]
        correct = confusion[actual][actual]
        correct_total += correct
        per_class[actual] = {
            "count": total,
            "correct": correct,
            "recall_with_abstentions": (correct / total) if total else 0.0,
            "low_confidence": confusion[actual]["low_confidence"],
        }
    return {
        "schema_version": 2,
        "model": info,
        "data": str(test_directory.resolve()),
        "minimum_confidence": float(minimum_confidence),
        "minimum_margin": float(minimum_margin),
        "tta_quadrants": bool(tta_quadrants),
        "total_images": total_count,
        "accepted_images": accepted_count,
        "acceptance_rate": accepted_count / total_count,
        "accuracy_with_abstentions": correct_total / total_count,
        "accepted_accuracy": correct_accepted / accepted_count if accepted_count else 0.0,
        "per_class": per_class,
        "confusion": confusion,
        "unreadable_files": unreadable_files,
    }


def train_classifier(
    dataset_directory: Path,
    output_root: Path,
    parameters: MlTrainingParameters,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    params = parameters.normalized()
    counts = {split: _class_counts(dataset_directory, split) for split in ("train", "val", "test")}
    dataset_summary: dict[str, Any] = {}
    summary_path = Path(dataset_directory) / "summary.json"
    if summary_path.is_file():
        try:
            dataset_summary = dict(json.loads(summary_path.read_text(encoding="utf-8")) or {})
        except (OSError, json.JSONDecodeError, TypeError):
            dataset_summary = {}
    missing_train = [label for label, count in counts["train"].items() if count == 0]
    if missing_train:
        raise MlTrainingError(
            "Four-class training needs at least one labeled training example for "
            "PLUS, MINUS, BLANK, and INVALID MARKING. Missing from TRAIN: "
            + ", ".join(label.replace("_", " ").upper() for label in missing_train)
            + ". Collection targets are advisory; only class coverage is required here."
        )
    if sum(counts["val"].values()) <= 0:
        raise MlTrainingError(
            "No independent validation images are available. Capture at least one additional varied camera frame so a leakage-safe validation group can be reserved. "
            "The collection targets themselves are not a hard stop."
        )

    env = training_environment()
    if not env.get("ready"):
        raise MlTrainingError(
            "Training runtime is not installed. Install requirements-training.txt in this virtual environment. "
            + "; ".join(str(item) for item in env.get("issues", []))
        )
    try:
        from ultralytics import YOLO  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise MlTrainingError(f"Ultralytics could not be loaded: {exc}") from exc

    run_id = f"{params.model_version.replace('.', '_')}-{uuid4().hex[:8]}"
    run_dir = Path(output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    def emit(stage: str, message: str, **extra: Any) -> None:
        if progress is not None:
            progress({"stage": stage, "message": message, **extra})

    emit("starting", "Loading base classification model", percent=2)
    model = YOLO(params.base_model)

    try:
        def on_train_epoch_end(trainer) -> None:  # type: ignore[no-untyped-def]
            epoch = int(getattr(trainer, "epoch", 0)) + 1
            percent = min(88, 5 + int(80 * epoch / max(1, params.epochs)))
            metrics = getattr(trainer, "metrics", {}) or {}
            try:
                metric_items = dict(metrics).items()
            except Exception:
                metric_items = ()
            emit(
                "training",
                f"Training epoch {epoch} of {params.epochs}",
                percent=percent,
                epoch=epoch,
                epochs=params.epochs,
                metrics={
                    str(k): float(v)
                    for k, v in metric_items
                    if isinstance(v, (int, float))
                },
            )

        model.add_callback("on_train_epoch_end", on_train_epoch_end)
    except Exception:  # noqa: S110 - callback contract varies by Ultralytics version; UI degrades
        # Older/newer Ultralytics callback contracts are allowed; the UI keeps an
        # indeterminate/phase progress display if epoch callbacks are unavailable.
        pass

    emit("training", "Training terminal-top classifier", percent=5)
    model.train(
        data=str(Path(dataset_directory).resolve()),
        epochs=params.epochs,
        imgsz=params.image_size,
        batch=params.batch,
        device=params.device,
        degrees=180.0,
        translate=0.05,
        scale=0.15,
        shear=3.0,
        perspective=0.0005,
        fliplr=0.5,
        flipud=0.5,
        hsv_h=0.015,
        hsv_s=0.25,
        hsv_v=0.30,
        project=str(run_dir / "training_runs"),
        name="train",
        exist_ok=True,
    )
    best_path = Path(str(model.trainer.best))
    if not best_path.is_file():
        raise MlTrainingError(f"Training completed but best weights were not found: {best_path}")

    emit("exporting", "Exporting best model to ONNX", percent=90)
    best = YOLO(str(best_path))
    names_raw = best.names
    if isinstance(names_raw, dict):
        classes = [str(names_raw[index]).strip().lower() for index in sorted(names_raw)]
    else:
        classes = [str(item).strip().lower() for item in names_raw]
    if set(classes) != set(TRAINING_LABELS):
        raise MlTrainingError(f"Trained model classes do not match required classes: {classes}")
    exported = Path(
        str(
            best.export(
                format="onnx",
                imgsz=params.image_size,
                dynamic=False,
                simplify=False,
                opset=17,
            )
        )
    )
    model_path = run_dir / "polarity_classifier.onnx"
    shutil.copy2(exported, model_path)
    digest = sha256_file(model_path)
    manifest = {
        "schema_version": 1,
        "model_id": params.model_id,
        "model_version": params.model_version,
        "classes": classes,
        "input_size": [params.image_size, params.image_size],
        "model_sha256": digest,
        "onnx_file": model_path.name,
        "source": "guided_hmi_ultralytics_yolo_classification",
        "preprocess": {
            "color_order": "RGB",
            "scale": 1.0 / 255.0,
            "mean": [0.0, 0.0, 0.0],
            "std": [1.0, 1.0, 1.0],
        },
        "metadata": {
            "trained_at_utc": _utc_now(),
            "base_model": params.base_model,
            "dataset": str(Path(dataset_directory).resolve()),
            "counts": counts,
            "best_weights": str(best_path.resolve()),
            "rotation_augmentation_degrees": 180.0,
            "training_parameters": params.to_dict(),
            "input_crop_contract": str(
                dataset_summary.get("preferred_crop_contract", "taught_circle_masked_square_v1")
            ),
            "dataset_crop_contract_counts": dict(
                dataset_summary.get("crop_contract_counts") or {}
            ),
            "production_note": (
                "A trained candidate must pass held-out testing and recipe validation before production activation."
            ),
        },
    }
    manifest_path = run_dir / "polarity_classifier.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    test_total = sum(counts["test"].values())
    if test_total > 0:
        emit("evaluating", "Running held-out ONNX challenge set", percent=95)
        evaluation = evaluate_onnx_model(
            model_path,
            manifest_path,
            Path(dataset_directory) / "test",
            minimum_confidence=params.minimum_confidence,
            minimum_margin=params.minimum_margin,
            tta_quadrants=params.tta_quadrants,
        )
        evaluation["held_out_available"] = True
    else:
        emit("evaluating", "No held-out group available; candidate will not be installable yet", percent=95)
        evaluation = {
            "schema_version": 2,
            "model": OnnxPolarityModel(model_path, manifest_path).info(require_runtime=True),
            "data": str((Path(dataset_directory) / "test").resolve()),
            "minimum_confidence": float(params.minimum_confidence),
            "minimum_margin": float(params.minimum_margin),
            "tta_quadrants": bool(params.tta_quadrants),
            "total_images": 0,
            "accepted_images": 0,
            "acceptance_rate": 0.0,
            "accuracy_with_abstentions": 0.0,
            "accepted_accuracy": 0.0,
            "per_class": {
                label: {"count": 0, "correct": 0, "recall_with_abstentions": 0.0, "low_confidence": 0}
                for label in TRAINING_LABELS
            },
            "confusion": {
                actual: {predicted: 0 for predicted in list(TRAINING_LABELS) + ["low_confidence"]}
                for actual in TRAINING_LABELS
            },
            "unreadable_files": [],
            "held_out_available": False,
            "note": "No independent test group was available. Train completed, but candidate installation remains disabled until held-out evaluation data exists.",
        }
    evaluation_path = run_dir / "evaluation.json"
    evaluation_path.write_text(json.dumps(evaluation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    emit("complete", "Training and held-out evaluation complete", percent=100)
    return {
        "run_id": run_id,
        "run_directory": str(run_dir.resolve()),
        "model_path": str(model_path.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "evaluation_path": str(evaluation_path.resolve()),
        "model_sha256": digest,
        "parameters": params.to_dict(),
        "counts": counts,
        "evaluation": evaluation,
    }
