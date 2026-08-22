from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np


REQUIRED_POLARITY_CLASSES = ("plus", "minus", "blank", "invalid_marking")
LEGACY_THREE_CLASS_CONTRACT = ("plus", "minus", "blank")
LEGACY_UNREADABLE_CONTRACT = ("plus", "minus", "blank", "unreadable")
SUPPORTED_POLARITY_CLASSES = tuple(
    dict.fromkeys(REQUIRED_POLARITY_CLASSES + LEGACY_UNREADABLE_CONTRACT)
)
ACCEPTED_POLARITY_CLASS_CONTRACTS = (
    frozenset(REQUIRED_POLARITY_CLASSES),
    frozenset(LEGACY_THREE_CLASS_CONTRACT),
    frozenset(LEGACY_UNREADABLE_CONTRACT),
)


class MlModelError(RuntimeError):
    """Raised when a deployed polarity model package cannot be used safely."""


@dataclass(frozen=True, slots=True)
class MlModelManifest:
    schema_version: int
    model_id: str
    model_version: str
    classes: tuple[str, ...]
    input_size: tuple[int, int]
    model_sha256: str
    onnx_file: str = ""
    input_name: str = ""
    output_name: str = ""
    color_order: str = "RGB"
    scale: float = 1.0 / 255.0
    mean: tuple[float, float, float] = (0.0, 0.0, 0.0)
    std: tuple[float, float, float] = (1.0, 1.0, 1.0)
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MlModelManifest":
        classes_raw = payload.get("classes", ())
        if isinstance(classes_raw, dict):
            try:
                classes_raw = [
                    value
                    for _key, value in sorted(
                        classes_raw.items(), key=lambda item: int(item[0])
                    )
                ]
            except (TypeError, ValueError):
                classes_raw = list(classes_raw.values())
        classes = tuple(str(item).strip().lower() for item in classes_raw)

        input_raw = payload.get("input_size", 224)
        if isinstance(input_raw, (list, tuple)) and len(input_raw) >= 2:
            input_size = (int(input_raw[0]), int(input_raw[1]))
        else:
            size = int(input_raw or 224)
            input_size = (size, size)

        preprocess = dict(payload.get("preprocess") or {})
        mean_raw = preprocess.get("mean", payload.get("mean", (0.0, 0.0, 0.0)))
        std_raw = preprocess.get("std", payload.get("std", (1.0, 1.0, 1.0)))
        mean = tuple(float(item) for item in list(mean_raw)[:3])
        std = tuple(max(1e-9, float(item)) for item in list(std_raw)[:3])
        if len(mean) != 3 or len(std) != 3:
            raise MlModelError("ML manifest mean/std must each contain three values")

        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            model_id=str(payload.get("model_id", "")).strip(),
            model_version=str(payload.get("model_version", "")).strip(),
            classes=classes,
            input_size=(max(32, input_size[0]), max(32, input_size[1])),
            model_sha256=str(
                payload.get("model_sha256", payload.get("sha256", "")) or ""
            )
            .strip()
            .lower(),
            onnx_file=str(payload.get("onnx_file", "") or "").strip(),
            input_name=str(payload.get("input_name", "") or "").strip(),
            output_name=str(payload.get("output_name", "") or "").strip(),
            color_order=str(
                preprocess.get("color_order", payload.get("color_order", "RGB"))
            )
            .strip()
            .upper(),
            scale=float(preprocess.get("scale", payload.get("scale", 1.0 / 255.0))),
            mean=mean,  # type: ignore[arg-type]
            std=std,  # type: ignore[arg-type]
            source=str(payload.get("source", "") or "").strip(),
            metadata=dict(payload.get("metadata") or {}),
        )

    def validation_issues(self) -> list[str]:
        issues: list[str] = []
        if not self.model_id:
            issues.append("ML_MANIFEST_MODEL_ID_MISSING")
        if not self.model_version:
            issues.append("ML_MANIFEST_MODEL_VERSION_MISSING")
        if not self.model_sha256:
            issues.append("ML_MANIFEST_SHA256_MISSING")
        if len(set(self.classes)) != len(self.classes):
            issues.append("ML_MANIFEST_DUPLICATE_CLASSES")
        missing = [
            label for label in LEGACY_THREE_CLASS_CONTRACT if label not in self.classes
        ]
        if missing:
            issues.append("ML_MANIFEST_CLASSES_MISSING:" + ",".join(missing))
        unsupported = [label for label in self.classes if label not in set(SUPPORTED_POLARITY_CLASSES)]
        if unsupported:
            issues.append("ML_MANIFEST_UNSUPPORTED_CLASSES:" + ",".join(unsupported))
        class_set = frozenset(self.classes)
        if (
            class_set not in ACCEPTED_POLARITY_CLASS_CONTRACTS
            or len(self.classes) != len(class_set)
        ):
            issues.append(
                "ML_MANIFEST_CLASS_CONTRACT_INVALID:expected "
                "plus,minus,blank,invalid_marking (or a supported legacy contract)"
            )
        if self.color_order not in {"RGB", "BGR"}:
            issues.append("ML_MANIFEST_COLOR_ORDER_INVALID")
        return issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "classes": list(self.classes),
            "input_size": list(self.input_size),
            "model_sha256": self.model_sha256,
            "onnx_file": self.onnx_file,
            "input_name": self.input_name,
            "output_name": self.output_name,
            "source": self.source,
            "preprocess": {
                "color_order": self.color_order,
                "scale": self.scale,
                "mean": list(self.mean),
                "std": list(self.std),
            },
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class MlInference:
    probabilities: dict[str, float]
    top_label: str
    confidence: float
    margin: float
    tta_count: int
    input_size: tuple[int, int]


SessionFactory = Callable[[Path], Any]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class OnnxPolarityModel:
    """Small ONNX image-classification model used on isolated terminal tops.

    ONNX Runtime is imported lazily so the HMI can continue to run in legacy
    classifier mode on stations where the ML runtime has not yet been installed.
    Tests may inject a session factory, avoiding a hard dependency on the native
    runtime in CI.
    """

    def __init__(
        self,
        model_path: Path | str | None,
        manifest_path: Path | str | None,
        *,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self.model_path = Path(model_path).expanduser() if model_path else Path()
        self.manifest_path = (
            Path(manifest_path).expanduser() if manifest_path else Path()
        )
        self._session_factory = session_factory
        self._manifest: MlModelManifest | None = None
        self._session: Any | None = None
        self._load_error = ""
        self._actual_sha256 = ""
        self._input_name = ""
        self._output_name = ""

    @staticmethod
    def default_session_factory(model_path: Path) -> Any:
        try:
            import onnxruntime as ort  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on station package
            raise MlModelError(
                "ONNX Runtime is not installed. Install requirements.txt or "
                "`python -m pip install onnxruntime`."
            ) from exc
        providers = ["CPUExecutionProvider"]
        return ort.InferenceSession(str(model_path), providers=providers)

    @property
    def manifest(self) -> MlModelManifest | None:
        self._load_manifest_only()
        return self._manifest

    @property
    def actual_sha256(self) -> str:
        self._load_manifest_only()
        return self._actual_sha256

    @property
    def load_error(self) -> str:
        self._load_manifest_only()
        return self._load_error

    @property
    def ready(self) -> bool:
        return not self.readiness_issues(require_runtime=True)

    def _load_manifest_only(self) -> None:
        if self._manifest is not None or self._load_error:
            return
        if not self.model_path or str(self.model_path) == ".":
            self._load_error = "ML model path is not configured"
            return
        if not self.manifest_path or str(self.manifest_path) == ".":
            self._load_error = "ML model manifest path is not configured"
            return
        if not self.model_path.is_file():
            self._load_error = f"ML model file not found: {self.model_path}"
            return
        if not self.manifest_path.is_file():
            self._load_error = f"ML manifest file not found: {self.manifest_path}"
            return
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise MlModelError("ML manifest root must be a JSON object")
            manifest = MlModelManifest.from_dict(payload)
            issues = manifest.validation_issues()
            if issues:
                raise MlModelError("; ".join(issues))
            actual_sha = sha256_file(self.model_path)
            if manifest.model_sha256 != actual_sha:
                raise MlModelError(
                    "ML model SHA-256 does not match the manifest: "
                    f"expected {manifest.model_sha256}, got {actual_sha}"
                )
            self._manifest = manifest
            self._actual_sha256 = actual_sha
        except Exception as exc:  # noqa: BLE001 - exposed as readiness issue
            self._load_error = str(exc)

    @staticmethod
    def _static_dimension(value: Any) -> int | None:
        if isinstance(value, (int, np.integer)) and int(value) > 0:
            return int(value)
        return None

    def _validate_session_contract(
        self,
        session: Any,
        input_meta: Any,
        output_meta: Any,
        input_name: str,
        output_name: str,
    ) -> None:
        """Verify tensor layout/class count before a model can be commissioned."""

        assert self._manifest is not None
        input_shape = list(getattr(input_meta, "shape", []) or [])
        width, height = self._manifest.input_size
        if input_shape:
            if len(input_shape) != 4:
                raise MlModelError(
                    "ONNX classifier input must be a 4-D NCHW tensor; "
                    f"reported shape={input_shape}"
                )
            channels = self._static_dimension(input_shape[1])
            model_height = self._static_dimension(input_shape[2])
            model_width = self._static_dimension(input_shape[3])
            if channels not in {None, 3}:
                raise MlModelError(
                    f"ONNX classifier expects {channels} input channels; 3 are required"
                )
            if model_height not in {None, height} or model_width not in {None, width}:
                raise MlModelError(
                    "ONNX input size does not match manifest: "
                    f"model={model_width}x{model_height}, manifest={width}x{height}"
                )

        output_shape = list(getattr(output_meta, "shape", []) or [])
        if output_shape:
            static_class_count = self._static_dimension(output_shape[-1])
            if static_class_count not in {None, len(self._manifest.classes)}:
                raise MlModelError(
                    "ONNX output class count does not match manifest: "
                    f"{static_class_count} vs {len(self._manifest.classes)}"
                )

        # Run one neutral frame during APPLY & TEST.  This catches malformed or
        # incompatible exports before a recipe can bind to the model package.
        dummy = np.zeros((1, 3, height, width), dtype=np.float32)
        probe = session.run([output_name], {input_name: dummy})
        if not probe:
            raise MlModelError("ONNX classifier returned no output during self-test")
        array = np.asarray(probe[0])
        if array.ndim == 1:
            class_count = int(array.shape[0])
        else:
            if array.shape[0] != 1:
                raise MlModelError(
                    "ONNX classifier self-test returned an unexpected batch size: "
                    f"{array.shape}"
                )
            class_count = int(np.prod(array.shape[1:]))
        if class_count != len(self._manifest.classes):
            raise MlModelError(
                "ONNX classifier self-test class count does not match manifest: "
                f"{class_count} vs {len(self._manifest.classes)}"
            )
        if not np.all(np.isfinite(array)):
            raise MlModelError("ONNX classifier self-test returned NaN or infinity")

    def _ensure_session(self) -> None:
        self._load_manifest_only()
        if self._load_error:
            raise MlModelError(self._load_error)
        if self._session is not None:
            return
        assert self._manifest is not None
        factory = self._session_factory or self.default_session_factory
        try:
            session = factory(self.model_path)
            inputs = list(session.get_inputs())
            outputs = list(session.get_outputs())
            if not inputs or not outputs:
                raise MlModelError("ONNX model does not expose an input and output tensor")
            input_name = self._manifest.input_name or str(inputs[0].name)
            output_name = self._manifest.output_name or str(outputs[0].name)
            self._validate_session_contract(
                session, inputs[0], outputs[0], input_name, output_name
            )
            self._input_name = input_name
            self._output_name = output_name
            self._session = session
        except Exception as exc:  # noqa: BLE001 - native/runtime errors vary
            self._load_error = str(exc)
            raise MlModelError(self._load_error) from exc

    def readiness_issues(self, *, require_runtime: bool = True) -> list[str]:
        self._load_manifest_only()
        if self._load_error:
            return ["ML_MODEL_NOT_READY:" + self._load_error]
        if require_runtime:
            try:
                self._ensure_session()
            except MlModelError as exc:
                return ["ML_RUNTIME_NOT_READY:" + str(exc)]
        return []

    def info(self, *, require_runtime: bool = False) -> dict[str, Any]:
        issues = self.readiness_issues(require_runtime=require_runtime)
        manifest = self._manifest
        crop_contract = ""
        if manifest is not None:
            crop_contract = str(
                manifest.metadata.get("input_crop_contract", "legacy_rect_v1")
                or "legacy_rect_v1"
            )
        return {
            "ready": not issues,
            "runtime_checked": bool(require_runtime),
            "session_loaded": self._session is not None,
            "issues": issues,
            "model_path": str(self.model_path) if self.model_path else "",
            "manifest_path": str(self.manifest_path) if self.manifest_path else "",
            "model_id": manifest.model_id if manifest else "",
            "model_version": manifest.model_version if manifest else "",
            "model_sha256": self._actual_sha256,
            "classes": list(manifest.classes) if manifest else [],
            "input_size": list(manifest.input_size) if manifest else [],
            "source": manifest.source if manifest else "",
            "input_crop_contract": crop_contract,
            "runtime": "ONNX Runtime / CPUExecutionProvider",
        }

    @staticmethod
    def _softmax(values: np.ndarray) -> np.ndarray:
        values = values.astype(np.float64)
        shifted = values - np.max(values, axis=1, keepdims=True)
        exp = np.exp(shifted)
        denominator = np.sum(exp, axis=1, keepdims=True)
        return (exp / np.maximum(denominator, 1e-12)).astype(np.float32)

    def _preprocess_one(self, image_bgr: np.ndarray) -> np.ndarray:
        if image_bgr is None or image_bgr.size == 0:
            raise MlModelError("ML classifier received an empty terminal-top crop")
        assert self._manifest is not None
        width, height = self._manifest.input_size
        if image_bgr.ndim == 2:
            image = cv2.cvtColor(image_bgr, cv2.COLOR_GRAY2BGR)
        elif image_bgr.ndim == 3 and image_bgr.shape[2] >= 3:
            image = image_bgr[:, :, :3]
        else:
            raise MlModelError(f"Unsupported ML input image shape: {image_bgr.shape}")
        resized = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        if self._manifest.color_order == "RGB":
            resized = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        tensor = resized.astype(np.float32) * float(self._manifest.scale)
        mean = np.asarray(self._manifest.mean, dtype=np.float32).reshape(1, 1, 3)
        std = np.asarray(self._manifest.std, dtype=np.float32).reshape(1, 1, 3)
        tensor = (tensor - mean) / std
        return np.transpose(tensor, (2, 0, 1)).astype(np.float32)

    def infer(self, image_bgr: np.ndarray, *, tta_quadrants: bool = False) -> MlInference:
        self._ensure_session()
        assert self._session is not None
        assert self._manifest is not None

        rotations = (0, 1, 2, 3) if tta_quadrants else (0,)
        # Run one image at a time.  Ultralytics classification ONNX exports are
        # commonly fixed at batch=1 when dynamic export is disabled; sequential
        # quadrant TTA therefore works with both fixed and dynamic models.
        rows: list[np.ndarray] = []
        for k in rotations:
            current = np.ascontiguousarray(np.rot90(image_bgr, k)) if k else image_bgr
            tensor = self._preprocess_one(current)[None, ...]
            outputs = self._session.run(
                [self._output_name],
                {self._input_name: tensor},
            )
            if not outputs:
                raise MlModelError("ONNX classifier returned no output tensor")
            row = np.asarray(outputs[0], dtype=np.float32)
            if row.ndim == 1:
                row = row.reshape(1, -1)
            elif row.ndim > 2:
                row = row.reshape(row.shape[0], -1)
            if row.shape[0] != 1:
                raise MlModelError(
                    f"ONNX classifier returned batch {row.shape[0]} for a single input image"
                )
            rows.append(row[0])
        raw = np.stack(rows, axis=0)
        if raw.shape[1] != len(self._manifest.classes):
            raise MlModelError(
                "ONNX output class count does not match manifest: "
                f"{raw.shape[1]} vs {len(self._manifest.classes)}"
            )

        sums = np.sum(raw, axis=1)
        already_probabilities = bool(
            np.all(raw >= -1e-6)
            and np.all(raw <= 1.000001)
            and np.all(np.abs(sums - 1.0) < 0.02)
        )
        probabilities = raw if already_probabilities else self._softmax(raw)
        average = np.mean(probabilities, axis=0)
        order = np.argsort(average)[::-1]
        top_index = int(order[0])
        second = float(average[int(order[1])]) if len(order) > 1 else 0.0
        top = float(average[top_index])
        scores = {
            label: float(average[index])
            for index, label in enumerate(self._manifest.classes)
        }
        return MlInference(
            probabilities=scores,
            top_label=self._manifest.classes[top_index],
            confidence=top,
            margin=max(0.0, top - second),
            tta_count=len(rotations),
            input_size=self._manifest.input_size,
        )
