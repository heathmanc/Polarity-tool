from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_CLASSES = {"plus", "minus", "blank", "invalid_marking"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def class_counts(dataset: Path, split: str) -> dict[str, int]:
    root = dataset / split
    return {
        label: len(
            [
                path
                for path in (root / label).glob("*")
                if path.is_file()
                and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
            ]
        )
        for label in sorted(REQUIRED_CLASSES)
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Train a YOLO image-classification model on isolated battery terminal tops "
            "and export a production ONNX package."
        )
    )
    parser.add_argument("--data", type=Path, default=Path("dataset") / "polarity_cls")
    parser.add_argument("--base-model", default="yolo11n-cls.pt")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--imgsz", type=int, default=224)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--model-id", default="polarity-terminal-top-yolo")
    parser.add_argument(
        "--model-version",
        default=datetime.now(timezone.utc).strftime("%Y.%m.%d.%H%M"),
    )
    parser.add_argument("--output", type=Path, default=Path("models"))
    args = parser.parse_args()

    counts = {split: class_counts(args.data, split) for split in ("train", "val", "test")}
    missing_train = [label for label, count in counts["train"].items() if count == 0]
    if missing_train:
        raise SystemExit(
            "Dataset is not ready. PLUS, MINUS, BLANK, and INVALID_MARKING must "
            "each appear in TRAIN. "
            f"Missing train={missing_train}. Counts={counts}"
        )
    if sum(counts["val"].values()) <= 0:
        raise SystemExit(
            "Dataset is not ready. At least one independent validation capture group "
            "must be reserved. Collection targets and complete per-class validation "
            f"coverage are advisory. Counts={counts}"
        )

    dataset_summary: dict[str, object] = {}
    summary_path = args.data / "summary.json"
    if summary_path.is_file():
        try:
            dataset_summary = dict(json.loads(summary_path.read_text(encoding="utf-8")) or {})
        except (OSError, json.JSONDecodeError, TypeError):
            dataset_summary = {}

    try:
        from ultralytics import YOLO
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Ultralytics training dependencies are not installed. Run: "
            "python -m pip install -r requirements-training.txt"
        ) from exc

    model = YOLO(args.base_model)
    model.train(
        data=str(args.data),
        epochs=max(1, args.epochs),
        imgsz=max(96, args.imgsz),
        batch=args.batch,
        device=args.device,
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
        project=str(args.output / "training_runs"),
        name=args.model_version.replace(".", "_"),
    )

    best_path = Path(str(model.trainer.best))
    best = YOLO(str(best_path))
    names_raw = best.names
    if isinstance(names_raw, dict):
        classes = [str(names_raw[index]).strip().lower() for index in sorted(names_raw)]
    else:
        classes = [str(item).strip().lower() for item in names_raw]
    if set(classes) != REQUIRED_CLASSES:
        raise SystemExit(
            "Trained model classes do not match the required polarity classes: "
            f"{classes}"
        )

    exported = Path(
        str(
            best.export(
                format="onnx",
                imgsz=max(96, args.imgsz),
                dynamic=False,
                simplify=False,
                opset=17,
            )
        )
    )
    args.output.mkdir(parents=True, exist_ok=True)
    destination_model = args.output / "polarity_classifier.onnx"
    shutil.copy2(exported, destination_model)
    model_hash = sha256_file(destination_model)

    manifest = {
        "schema_version": 1,
        "model_id": args.model_id,
        "model_version": args.model_version,
        "classes": classes,
        "input_size": [max(96, args.imgsz), max(96, args.imgsz)],
        "model_sha256": model_hash,
        "onnx_file": destination_model.name,
        "source": "ultralytics_yolo_classification",
        "preprocess": {
            "color_order": "RGB",
            "scale": 1.0 / 255.0,
            "mean": [0.0, 0.0, 0.0],
            "std": [1.0, 1.0, 1.0],
        },
        "metadata": {
            "trained_at_utc": datetime.now(timezone.utc).isoformat(),
            "base_model": args.base_model,
            "dataset": str(args.data.resolve()),
            "counts": counts,
            "best_weights": str(best_path.resolve()),
            "rotation_augmentation_degrees": 180.0,
            "input_crop_contract": str(
                dataset_summary.get(
                    "preferred_crop_contract",
                    "taught_circle_masked_square_v1",
                )
            ),
            "dataset_crop_contract_counts": dict(
                dataset_summary.get("crop_contract_counts") or {}
            ),
            "production_note": (
                "This package must be challenged on held-out real battery images and "
                "validated through each production recipe before activation."
            ),
        },
    }
    manifest_path = args.output / "polarity_classifier.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"model": str(destination_model.resolve()), "manifest": str(manifest_path.resolve()), "sha256": model_hash, "classes": classes, "counts": counts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
