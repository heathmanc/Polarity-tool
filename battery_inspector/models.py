from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from battery_inspector.build_info import INSPECTION_ENGINE, RECORD_SCHEMA_VERSION


def _compatible_dataclass_payload(
    cls: type[Any],
    data: dict[str, Any] | None,
    *,
    aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return persisted settings that are compatible with ``cls``.

    Recipe settings live in SQLite as JSON and must remain readable across
    application upgrades.  Older releases may use renamed keys, while newer
    releases may add keys that an older build does not know about.  Translate
    known aliases and ignore unknown keys instead of preventing the HMI from
    starting.
    """

    payload = dict(data or {})
    for legacy_name, current_name in (aliases or {}).items():
        if legacy_name in payload and current_name not in payload:
            payload[current_name] = payload[legacy_name]

    accepted = {item.name for item in fields(cls)}
    return {key: value for key, value in payload.items() if key in accepted}


class Marking(StrEnum):
    PLUS = "plus"
    MINUS = "minus"
    BLANK = "blank"
    INVALID_MARKING = "invalid_marking"
    OTHER = "other"
    UNREADABLE = "unreadable"

    @property
    def display(self) -> str:
        return {
            Marking.PLUS: "PLUS",
            Marking.MINUS: "MINUS",
            Marking.BLANK: "BLANK",
            Marking.INVALID_MARKING: "INVALID MARKING",
            Marking.OTHER: "OTHER / MISMATCH",
            Marking.UNREADABLE: "UNREADABLE",
        }[self]


class TerminalRole(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    AUXILIARY = "auxiliary"

    @property
    def display(self) -> str:
        return self.value.upper()


class TerminalFinish(StrEnum):
    """Recipe expectation for the visible terminal-top material finish."""

    UNSPECIFIED = "unspecified"
    SILVER = "silver"
    BRASS = "brass"

    @property
    def display(self) -> str:
        return {
            TerminalFinish.UNSPECIFIED: "NOT CONFIGURED",
            TerminalFinish.SILVER: "SILVER",
            TerminalFinish.BRASS: "BRASS",
        }[self]


class RecipeStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    ACTIVE = "active"
    RETIRED = "retired"


class InspectionDisposition(StrEnum):
    PASS = "pass"
    REJECT = "reject"
    NOT_READY = "not_ready"
    INDETERMINATE = "indeterminate"
    SYSTEM_FAULT = "system_fault"

    @property
    def display(self) -> str:
        return {
            InspectionDisposition.PASS: "PASS",
            InspectionDisposition.REJECT: "REJECT",
            InspectionDisposition.NOT_READY: "NOT READY",
            InspectionDisposition.INDETERMINATE: "INDETERMINATE",
            InspectionDisposition.SYSTEM_FAULT: "SYSTEM FAULT",
        }[self]


class InspectionCycleState(StrEnum):
    IDLE = "idle"
    ACQUIRING = "acquiring"
    LOCATING = "locating"
    INSPECTING = "inspecting"
    SAVING = "saving"
    COMPLETE = "complete"
    NOT_READY = "not_ready"
    FAULT = "fault"

    @property
    def display(self) -> str:
        return self.value.replace("_", " ").upper()

    @property
    def active(self) -> bool:
        return self in {
            InspectionCycleState.ACQUIRING,
            InspectionCycleState.LOCATING,
            InspectionCycleState.INSPECTING,
            InspectionCycleState.SAVING,
        }


@dataclass(slots=True)
class InspectionCycleStatus:
    cycle_id: str
    state: InspectionCycleState
    trigger_source: str
    message: str
    started_at_utc: str
    capture_id: str = ""
    frame_id: str = ""
    frame_sequence: int = 0
    captured_at_utc: str = ""
    updated_at_utc: str = ""

    @classmethod
    def idle(cls) -> "InspectionCycleStatus":
        return cls(
            cycle_id="",
            state=InspectionCycleState.IDLE,
            trigger_source="",
            message="Ready for trigger",
            started_at_utc="",
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
        )

    def with_state(
        self,
        state: InspectionCycleState,
        message: str,
        **changes: Any,
    ) -> "InspectionCycleStatus":
        return replace(
            self,
            state=state,
            message=message,
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
            **changes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "state": self.state.value,
            "trigger_source": self.trigger_source,
            "message": self.message,
            "started_at_utc": self.started_at_utc,
            "capture_id": self.capture_id,
            "frame_id": self.frame_id,
            "frame_sequence": self.frame_sequence,
            "captured_at_utc": self.captured_at_utc,
            "updated_at_utc": self.updated_at_utc,
        }


@dataclass(slots=True)
class NormalizedRect:
    """Rectangle expressed in normalized coordinates (0.0 to 1.0)."""

    x: float
    y: float
    width: float
    height: float

    def clamped(self) -> "NormalizedRect":
        x = min(max(float(self.x), 0.0), 1.0)
        y = min(max(float(self.y), 0.0), 1.0)
        width = min(max(float(self.width), 0.001), max(0.001, 1.0 - x))
        height = min(max(float(self.height), 0.001), max(0.001, 1.0 - y))
        return NormalizedRect(x=x, y=y, width=width, height=height)

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NormalizedRect":
        return cls(
            x=float(data["x"]),
            y=float(data["y"]),
            width=float(data["width"]),
            height=float(data["height"]),
        ).clamped()


@dataclass(slots=True)
class LocatorSettings:
    """Recipe-portable feature-registration limits.

    These values are deliberately expressed as algorithm-neutral acceptance
    limits. A future YOLO/OBB locator can honor the same recipe contract while
    the first production implementation uses OpenCV reference registration.
    """

    method: str = "reference_features"
    detector: str = "AUTO"  # AUTO | SIFT | ORB
    max_detection_dimension: int = 1600
    feature_count: int = 4000
    match_ratio: float = 0.72
    ransac_threshold_px: float = 8.0
    minimum_matches: int = 18
    minimum_inliers: int = 12
    minimum_inlier_ratio: float = 0.35
    maximum_median_error_px: float = 8.0
    minimum_scale: float = 0.65
    maximum_scale: float = 1.45
    minimum_visible_fraction: float = 0.85
    minimum_orientation_margin: float = 0.035
    orientation_max_dimension: int = 512

    def normalized(self) -> "LocatorSettings":
        detector = str(self.detector or "AUTO").upper()
        if detector not in {"AUTO", "SIFT", "ORB"}:
            detector = "AUTO"
        return LocatorSettings(
            method=str(self.method or "reference_features"),
            detector=detector,
            max_detection_dimension=max(640, int(self.max_detection_dimension)),
            feature_count=max(500, int(self.feature_count)),
            match_ratio=min(0.95, max(0.40, float(self.match_ratio))),
            ransac_threshold_px=max(1.0, float(self.ransac_threshold_px)),
            minimum_matches=max(6, int(self.minimum_matches)),
            minimum_inliers=max(4, int(self.minimum_inliers)),
            minimum_inlier_ratio=min(1.0, max(0.05, float(self.minimum_inlier_ratio))),
            maximum_median_error_px=max(1.0, float(self.maximum_median_error_px)),
            minimum_scale=max(0.10, float(self.minimum_scale)),
            maximum_scale=max(float(self.minimum_scale) + 0.05, float(self.maximum_scale)),
            minimum_visible_fraction=min(1.0, max(0.20, float(self.minimum_visible_fraction))),
            minimum_orientation_margin=min(
                0.50, max(0.0, float(self.minimum_orientation_margin))
            ),
            orientation_max_dimension=max(128, int(self.orientation_max_dimension)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalized())

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LocatorSettings":
        return cls(**_compatible_dataclass_payload(cls, data)).normalized()


@dataclass(slots=True)
class MarkingClassifierSettings:
    """Per-recipe limits for polarity-mark recognition.

    ``onnx_ml`` is the preferred production method once a validated station
    model is installed.  The recipe stores the model identity and SHA-256 hash
    that were present during guided validation.  Replacing the station model
    therefore requires a new recipe revision/validation rather than silently
    changing an active inspection. ``reference_template`` remains available as
    a conservative legacy method and ``geometric_stamp`` as an engineering
    comparison mode.
    """

    method: str = "reference_template"  # onnx_ml | reference_template | geometric_stamp
    normalized_size_px: int = 256
    minimum_contrast: float = 12.0
    minimum_sharpness: float = 5.0
    maximum_clipped_fraction: float = 0.55
    minimum_confidence: float = 0.70

    # Rotation-invariant geometric stamp gates.
    blank_maximum_signal: float = 0.045
    plus_minimum_signal: float = 0.060
    plus_minimum_orthogonal_ratio: float = 0.45
    minus_minimum_signal: float = 0.100
    minus_maximum_orthogonal_ratio: float = 0.25

    # Optional reference-template comparison gates.
    acceptance_threshold: float = 0.58
    minimum_margin: float = 0.04
    maximum_residual_rotation_deg: float = 8.0
    rotation_step_deg: float = 4.0
    maximum_shift_px: int = 6

    # Rotation-invariant hybrid decision. Geometry is measured on the isolated
    # central terminal top; reference-template evidence confirms that the line
    # geometry resembles a taught production stamp rather than a random scratch.
    terminal_top_minimum_confidence: float = 0.80

    # A Hough-detected terminal top can be accepted conditionally when the
    # taught marking ROI is slightly off-center but the actual stamp geometry
    # is exceptionally strong and physically plausible.  This is intentionally
    # limited to PLUS/MINUS; BLANK still requires the nominal top-lock gate.
    terminal_top_conditional_minimum_confidence: float = 0.68
    terminal_top_conditional_geometry_confidence: float = 0.90
    terminal_top_conditional_minimum_center_score: float = 0.55
    terminal_top_conditional_minimum_inside_fraction: float = 0.90

    hybrid_geometry_weight: float = 0.75
    hybrid_minimum_template_confirmation: float = 0.12
    hybrid_conflict_template_threshold: float = 0.70

    # ONNX ML classifier binding and fail-closed decision gates.  The model file
    # itself is station configuration; these fields bind this recipe revision to
    # the exact model package that was validated.
    ml_model_id: str = ""
    ml_model_version: str = ""
    ml_model_sha256: str = ""
    ml_minimum_confidence: float = 0.90
    ml_minimum_margin: float = 0.15
    ml_center_fallback_minimum_confidence: float = 0.96
    ml_center_fallback_minimum_margin: float = 0.25
    ml_test_time_quadrants: bool = False

    # Physical input-validity gate.  The polarity classifier is not allowed to
    # grade an ROI until the current terminal-face crop is sufficiently
    # consistent with the same physical terminal in the known-good recipe
    # reference.  This prevents an open/missing terminal, fixture hole, or
    # grossly wrong object from receiving a confident PLUS/MINUS/BLANK result.
    # The comparison is deliberately low-frequency and rotation tolerant so
    # normal stamp rotation, scratches, and modest lighting variation do not
    # become marking decisions.
    terminal_face_validation_enabled: bool = True
    terminal_face_minimum_radial_correlation: float = 0.10
    terminal_face_minimum_structure_correlation: float = 0.10
    terminal_face_maximum_center_saturation_delta: float = 70.0
    terminal_face_maximum_center_value_delta: float = 85.0
    terminal_face_minimum_score: float = 0.35

    def normalized(self) -> "MarkingClassifierSettings":
        size = max(96, min(384, int(self.normalized_size_px)))
        method = str(self.method or "reference_template").strip().lower()
        if method not in {"onnx_ml", "reference_template", "geometric_stamp"}:
            method = "reference_template"
        return MarkingClassifierSettings(
            method=method,
            normalized_size_px=size,
            minimum_contrast=max(0.0, float(self.minimum_contrast)),
            minimum_sharpness=max(0.0, float(self.minimum_sharpness)),
            maximum_clipped_fraction=min(
                1.0, max(0.0, float(self.maximum_clipped_fraction))
            ),
            minimum_confidence=min(0.99, max(0.50, float(self.minimum_confidence))),
            blank_maximum_signal=max(0.0, float(self.blank_maximum_signal)),
            plus_minimum_signal=max(0.0, float(self.plus_minimum_signal)),
            plus_minimum_orthogonal_ratio=min(
                1.5, max(0.0, float(self.plus_minimum_orthogonal_ratio))
            ),
            minus_minimum_signal=max(0.0, float(self.minus_minimum_signal)),
            minus_maximum_orthogonal_ratio=min(
                1.0, max(0.0, float(self.minus_maximum_orthogonal_ratio))
            ),
            acceptance_threshold=min(
                0.98, max(0.20, float(self.acceptance_threshold))
            ),
            minimum_margin=min(0.50, max(0.0, float(self.minimum_margin))),
            maximum_residual_rotation_deg=max(
                0.0, min(30.0, float(self.maximum_residual_rotation_deg))
            ),
            rotation_step_deg=max(1.0, min(15.0, float(self.rotation_step_deg))),
            maximum_shift_px=max(0, min(size // 5, int(self.maximum_shift_px))),
            terminal_top_minimum_confidence=min(
                0.95, max(0.10, float(self.terminal_top_minimum_confidence))
            ),
            terminal_top_conditional_minimum_confidence=min(
                0.90,
                max(
                    0.10,
                    float(self.terminal_top_conditional_minimum_confidence),
                ),
            ),
            terminal_top_conditional_geometry_confidence=min(
                0.99,
                max(
                    0.50,
                    float(self.terminal_top_conditional_geometry_confidence),
                ),
            ),
            terminal_top_conditional_minimum_center_score=min(
                1.0,
                max(
                    0.0,
                    float(self.terminal_top_conditional_minimum_center_score),
                ),
            ),
            terminal_top_conditional_minimum_inside_fraction=min(
                1.0,
                max(
                    0.0,
                    float(self.terminal_top_conditional_minimum_inside_fraction),
                ),
            ),
            hybrid_geometry_weight=min(
                0.95, max(0.50, float(self.hybrid_geometry_weight))
            ),
            hybrid_minimum_template_confirmation=min(
                0.80, max(0.0, float(self.hybrid_minimum_template_confirmation))
            ),
            hybrid_conflict_template_threshold=min(
                0.99, max(0.40, float(self.hybrid_conflict_template_threshold))
            ),
            ml_model_id=str(self.ml_model_id or "").strip(),
            ml_model_version=str(self.ml_model_version or "").strip(),
            ml_model_sha256=str(self.ml_model_sha256 or "").strip().lower(),
            ml_minimum_confidence=min(
                0.999, max(0.50, float(self.ml_minimum_confidence))
            ),
            ml_minimum_margin=min(
                0.80, max(0.0, float(self.ml_minimum_margin))
            ),
            ml_center_fallback_minimum_confidence=min(
                0.999,
                max(0.50, float(self.ml_center_fallback_minimum_confidence)),
            ),
            ml_center_fallback_minimum_margin=min(
                0.90,
                max(0.0, float(self.ml_center_fallback_minimum_margin)),
            ),
            ml_test_time_quadrants=bool(self.ml_test_time_quadrants),
            terminal_face_validation_enabled=bool(
                self.terminal_face_validation_enabled
            ),
            terminal_face_minimum_radial_correlation=min(
                0.95,
                max(-0.50, float(self.terminal_face_minimum_radial_correlation)),
            ),
            terminal_face_minimum_structure_correlation=min(
                0.95,
                max(-0.50, float(self.terminal_face_minimum_structure_correlation)),
            ),
            terminal_face_maximum_center_saturation_delta=min(
                255.0,
                max(0.0, float(self.terminal_face_maximum_center_saturation_delta)),
            ),
            terminal_face_maximum_center_value_delta=min(
                255.0,
                max(0.0, float(self.terminal_face_maximum_center_value_delta)),
            ),
            terminal_face_minimum_score=min(
                0.99, max(0.0, float(self.terminal_face_minimum_score))
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalized())

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MarkingClassifierSettings":
        payload = _compatible_dataclass_payload(
            cls,
            data,
            aliases={
                # v0.8.1 persisted this longer name.  v0.9.0 shortened the
                # Python attribute without a database migration, which caused
                # startup to fail while loading an existing recipe database.
                "terminal_top_conditional_minimum_geometry_confidence": (
                    "terminal_top_conditional_geometry_confidence"
                ),
            },
        )
        return cls(**payload).normalized()


@dataclass(slots=True)
class TerminalRecipe:
    key: str
    name: str
    role: TerminalRole
    search_roi: NormalizedRect
    marking_roi: NormalizedRect
    expected_marking: Marking
    red_ring_required: bool
    expected_finish: TerminalFinish = TerminalFinish.UNSPECIFIED
    marking_roi_shape: str = "rectangle"
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "role": self.role.value,
            "search_roi": self.search_roi.to_dict(),
            "marking_roi": self.marking_roi.to_dict(),
            "expected_marking": self.expected_marking.value,
            "red_ring_required": self.red_ring_required,
            "expected_finish": self.expected_finish.value,
            "marking_roi_shape": self.marking_roi_shape,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TerminalRecipe":
        return cls(
            key=str(data["key"]),
            name=str(data["name"]),
            role=TerminalRole(data["role"]),
            search_roi=NormalizedRect.from_dict(data["search_roi"]),
            marking_roi=NormalizedRect.from_dict(data["marking_roi"]),
            expected_marking=Marking(data["expected_marking"]),
            red_ring_required=bool(data.get("red_ring_required", False)),
            expected_finish=TerminalFinish(
                data.get("expected_finish", TerminalFinish.UNSPECIFIED.value)
            ),
            marking_roi_shape=(
                "circle"
                if str(data.get("marking_roi_shape", "rectangle")).strip().lower()
                == "circle"
                else "rectangle"
            ),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass(slots=True)
class ReferenceCapture:
    capture_id: str
    path: str
    sha256: str
    captured_at_utc: str
    width_px: int
    height_px: int
    channels: int = 3
    frame_sequence: int = 0
    frame_id: str = ""
    camera_frame_id: str = ""
    camera_timestamp_raw: int | None = None
    source: str = "RECIPE_REFERENCE"
    camera_backend: str = ""
    camera_description: str = ""
    camera_profile: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)

    @property
    def resolution(self) -> tuple[int, int]:
        return self.width_px, self.height_px

    @property
    def quality_status(self) -> str:
        return str(self.quality.get("status", "UNKNOWN") or "UNKNOWN").strip().upper()

    @property
    def acceptable_for_recipe(self) -> bool:
        """Reject an explicitly poor reference while preserving legacy UNKNOWN records."""

        return self.quality_status != "POOR"

    def copied(self) -> "ReferenceCapture":
        return replace(
            self,
            camera_profile=dict(self.camera_profile),
            quality=dict(self.quality),
        )

    def clone(self) -> "ReferenceCapture":
        return self.copied()

    def to_dict(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "path": self.path,
            "sha256": self.sha256,
            "captured_at_utc": self.captured_at_utc,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "channels": self.channels,
            "frame_sequence": self.frame_sequence,
            "frame_id": self.frame_id,
            "camera_frame_id": self.camera_frame_id,
            "camera_timestamp_raw": self.camera_timestamp_raw,
            "source": self.source,
            "camera_backend": self.camera_backend,
            "camera_description": self.camera_description,
            "camera_profile": dict(self.camera_profile),
            "quality": dict(self.quality),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReferenceCapture":
        return cls(
            capture_id=str(data.get("capture_id", "")),
            path=str(data.get("path", "")),
            sha256=str(data.get("sha256", "")),
            captured_at_utc=str(data.get("captured_at_utc", "")),
            width_px=int(data.get("width_px", 0)),
            height_px=int(data.get("height_px", 0)),
            channels=int(data.get("channels", 3)),
            frame_sequence=int(data.get("frame_sequence", 0)),
            frame_id=str(data.get("frame_id", "")),
            camera_frame_id=str(data.get("camera_frame_id", "")),
            camera_timestamp_raw=(
                int(data.get("camera_timestamp_raw", data.get("camera_timestamp_ns")))
                if data.get("camera_timestamp_raw", data.get("camera_timestamp_ns")) is not None
                else None
            ),
            source=str(data.get("source", "RECIPE_REFERENCE")),
            camera_backend=str(data.get("camera_backend", "")),
            camera_description=str(data.get("camera_description", "")),
            camera_profile=dict(data.get("camera_profile", {}) or {}),
            quality=dict(data.get("quality", {}) or {}),
        )


@dataclass(slots=True)
class Recipe:
    recipe_id: str
    recipe_number: int
    name: str
    part_number: str
    description: str
    revision: int
    status: RecipeStatus
    battery_roi: NormalizedRect
    orientation_reference: str
    terminals: list[TerminalRecipe]
    created_by: str
    created_at_utc: str
    updated_by: str
    updated_at_utc: str
    validation_runs_required: int = 5
    validation_runs_passed: int = 0
    reference_image: ReferenceCapture | None = None
    locator_settings: LocatorSettings = field(default_factory=LocatorSettings)
    classifier_settings: MarkingClassifierSettings = field(
        default_factory=MarkingClassifierSettings
    )
    validation_records: list[dict[str, Any]] = field(default_factory=list)
    validation_configuration_hash: str = ""

    @classmethod
    def new(
        cls,
        *,
        name: str,
        recipe_number: int = 0,
        part_number: str,
        description: str,
        created_by: str,
        battery_roi: NormalizedRect,
        terminals: list[TerminalRecipe],
        orientation_reference: str = "battery_outline",
        reference_image: ReferenceCapture | None = None,
    ) -> "Recipe":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            recipe_id=str(uuid4()),
            recipe_number=max(0, int(recipe_number)),
            name=name,
            part_number=part_number,
            description=description,
            revision=1,
            status=RecipeStatus.DRAFT,
            battery_roi=battery_roi.clamped(),
            orientation_reference=orientation_reference,
            terminals=terminals,
            created_by=created_by,
            created_at_utc=now,
            updated_by=created_by,
            updated_at_utc=now,
            reference_image=reference_image.copied() if reference_image else None,
            locator_settings=LocatorSettings(),
            classifier_settings=MarkingClassifierSettings(),
        )

    @property
    def has_reference_image(self) -> bool:
        return bool(
            self.reference_image
            and self.reference_image.path
            and self.reference_image.sha256
            and self.reference_image.width_px > 0
            and self.reference_image.height_px > 0
        )

    @property
    def reference_image_path(self) -> str:
        return self.reference_image.path if self.reference_image else ""

    @property
    def reference_is_demo(self) -> bool:
        return bool(
            self.reference_image
            and self.reference_image.source.strip().upper() == "BUNDLED_DEMO_REFERENCE"
        )

    @property
    def reference_is_simulated(self) -> bool:
        if self.reference_image is None:
            return False
        backend = self.reference_image.camera_backend.strip().lower()
        return backend in {"mockcameraservice", "simulation", "bundled-asset"}

    @property
    def validation_pass_record_count(self) -> int:
        """Count configuration-bound PASS records eligible for activation.

        Older releases stored only a numeric pass count.  That is insufficient
        after the real locator/classifier were introduced because it cannot prove
        which reference, ROIs, labels, or engine settings were validated.
        """

        fingerprint = self.validation_configuration_hash.strip()
        if not fingerprint:
            return 0
        return sum(
            1
            for record in self.validation_records
            if str(record.get("disposition", "")).strip().lower() == "pass"
            and str(record.get("configuration_hash", "")).strip() == fingerprint
            and str(record.get("inspection_engine", "")).strip()
            == INSPECTION_ENGINE
        )

    @property
    def validation_complete(self) -> bool:
        required = max(1, int(self.validation_runs_required))
        return bool(
            self.validation_runs_passed >= required
            and self.validation_pass_record_count >= required
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "recipe_number": int(self.recipe_number),
            "name": self.name,
            "part_number": self.part_number,
            "description": self.description,
            "revision": self.revision,
            "status": self.status.value,
            "battery_roi": self.battery_roi.to_dict(),
            "orientation_reference": self.orientation_reference,
            "terminals": [terminal.to_dict() for terminal in self.terminals],
            "created_by": self.created_by,
            "created_at_utc": self.created_at_utc,
            "updated_by": self.updated_by,
            "updated_at_utc": self.updated_at_utc,
            "validation_runs_required": self.validation_runs_required,
            "validation_runs_passed": self.validation_runs_passed,
            "reference_image": self.reference_image.to_dict() if self.reference_image else None,
            "locator_settings": self.locator_settings.to_dict(),
            "classifier_settings": self.classifier_settings.to_dict(),
            "validation_records": [dict(item) for item in self.validation_records],
            "validation_configuration_hash": self.validation_configuration_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Recipe":
        reference_payload = data.get("reference_image")
        # Compatibility with development payloads that used flat reference fields.
        if not reference_payload and data.get("reference_image_path"):
            reference_payload = {
                "capture_id": f"{data.get('recipe_id', '')}-r{data.get('revision', 1)}",
                "path": data.get("reference_image_path", ""),
                "sha256": data.get("reference_image_sha256", ""),
                "captured_at_utc": data.get("reference_captured_at_utc", ""),
                "width_px": data.get("reference_width_px", 0),
                "height_px": data.get("reference_height_px", 0),
                "camera_profile": data.get("reference_camera_profile", {}),
            }
        return cls(
            recipe_id=str(data["recipe_id"]),
            recipe_number=max(0, int(data.get("recipe_number", 0) or 0)),
            name=str(data["name"]),
            part_number=str(data.get("part_number", "")),
            description=str(data.get("description", "")),
            revision=int(data.get("revision", 1)),
            status=RecipeStatus(data.get("status", RecipeStatus.DRAFT.value)),
            battery_roi=NormalizedRect.from_dict(data["battery_roi"]),
            orientation_reference=str(data.get("orientation_reference", "battery_outline")),
            terminals=[TerminalRecipe.from_dict(item) for item in data.get("terminals", [])],
            created_by=str(data.get("created_by", "unknown")),
            created_at_utc=str(data.get("created_at_utc", "")),
            updated_by=str(data.get("updated_by", "unknown")),
            updated_at_utc=str(data.get("updated_at_utc", "")),
            validation_runs_required=int(data.get("validation_runs_required", 5)),
            validation_runs_passed=int(data.get("validation_runs_passed", 0)),
            reference_image=(
                ReferenceCapture.from_dict(dict(reference_payload))
                if isinstance(reference_payload, dict)
                else None
            ),
            locator_settings=LocatorSettings.from_dict(data.get("locator_settings")),
            classifier_settings=MarkingClassifierSettings.from_dict(
                data.get("classifier_settings")
            ),
            validation_records=[
                dict(item)
                for item in list(data.get("validation_records", []) or [])
                if isinstance(item, dict)
            ],
            validation_configuration_hash=str(
                data.get("validation_configuration_hash", "")
            ),
        )


@dataclass(slots=True)
class TerminalInspection:
    terminal_key: str
    terminal_name: str
    role: TerminalRole
    expected_marking: Marking
    detected_marking: Marking
    marking_confidence: float
    red_ring_expected: bool
    red_ring_detected: bool
    red_ring_confidence: float
    expected_finish: TerminalFinish = TerminalFinish.UNSPECIFIED
    detected_finish: TerminalFinish = TerminalFinish.UNSPECIFIED
    finish_confidence: float = 0.0
    finish_evaluated: bool = False
    finish_status: str = "LEGACY_NOT_CONFIGURED"
    finish_note: str = ""
    finish_metrics: dict[str, Any] = field(default_factory=dict)
    terminal_crop_path: str | None = None
    marking_crop_path: str | None = None
    marking_evaluated: bool = True
    ring_evaluated: bool = True
    analysis_note: str = ""
    reference_marking_path: str | None = None
    reference_similarity: float = 0.0
    class_scores: dict[str, float] = field(default_factory=dict)
    classification_metrics: dict[str, Any] = field(default_factory=dict)
    classification_status: str = ""
    diagnostic_image_paths: dict[str, str] = field(default_factory=dict)
    terminal_polygon: list[tuple[float, float]] = field(default_factory=list)
    marking_polygon: list[tuple[float, float]] = field(default_factory=list)
    terminal_face_evaluated: bool = True
    terminal_face_present: bool = True
    terminal_face_confidence: float = 1.0
    terminal_face_status: str = "LEGACY_NOT_CHECKED"
    # Runtime-only images support a zero-write PASS path. They are deliberately
    # excluded from ``to_dict`` and are released when the HMI replaces the result.
    terminal_crop_image: Any | None = field(default=None, repr=False, compare=False)
    marking_crop_image: Any | None = field(default=None, repr=False, compare=False)
    reference_marking_image: Any | None = field(default=None, repr=False, compare=False)
    diagnostic_images: dict[str, Any] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    @property
    def terminal_face_pass(self) -> bool:
        return self.terminal_face_evaluated and self.terminal_face_present

    @property
    def marking_pass(self) -> bool:
        return self.marking_evaluated and self.detected_marking == self.expected_marking

    @property
    def ring_pass(self) -> bool:
        return self.ring_evaluated and self.red_ring_detected == self.red_ring_expected

    @property
    def finish_pass(self) -> bool:
        if self.expected_finish == TerminalFinish.UNSPECIFIED:
            return True
        return self.finish_evaluated and self.detected_finish == self.expected_finish

    @property
    def passed(self) -> bool:
        return (
            self.terminal_face_pass
            and self.finish_pass
            and self.marking_pass
            and self.ring_pass
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "terminal_key": self.terminal_key,
            "terminal_name": self.terminal_name,
            "role": self.role.value,
            "expected_marking": self.expected_marking.value,
            "detected_marking": self.detected_marking.value,
            "marking_confidence": self.marking_confidence,
            "red_ring_expected": self.red_ring_expected,
            "red_ring_detected": self.red_ring_detected,
            "red_ring_confidence": self.red_ring_confidence,
            "expected_finish": self.expected_finish.value,
            "detected_finish": self.detected_finish.value,
            "finish_confidence": self.finish_confidence,
            "finish_evaluated": self.finish_evaluated,
            "finish_status": self.finish_status,
            "finish_note": self.finish_note,
            "finish_metrics": dict(self.finish_metrics),
            "terminal_crop_path": self.terminal_crop_path,
            "marking_crop_path": self.marking_crop_path,
            "marking_evaluated": self.marking_evaluated,
            "ring_evaluated": self.ring_evaluated,
            "analysis_note": self.analysis_note,
            "reference_marking_path": self.reference_marking_path,
            "reference_similarity": self.reference_similarity,
            "class_scores": dict(self.class_scores),
            "classification_metrics": dict(self.classification_metrics),
            "classification_status": self.classification_status,
            "diagnostic_image_paths": dict(self.diagnostic_image_paths),
            "terminal_polygon": [list(point) for point in self.terminal_polygon],
            "marking_polygon": [list(point) for point in self.marking_polygon],
            "terminal_face_evaluated": self.terminal_face_evaluated,
            "terminal_face_present": self.terminal_face_present,
            "terminal_face_confidence": self.terminal_face_confidence,
            "terminal_face_status": self.terminal_face_status,
            "terminal_face_pass": self.terminal_face_pass,
            "finish_pass": self.finish_pass,
            "marking_pass": self.marking_pass,
            "ring_pass": self.ring_pass,
            "passed": self.passed,
        }


@dataclass(slots=True)
class InspectionResult:
    inspection_id: str
    recipe_id: str
    recipe_name: str
    timestamp_utc: str
    disposition: InspectionDisposition
    reason: str
    duration_ms: int
    trigger_source: str
    image_quality: str
    full_image_path: str
    battery_roi: NormalizedRect
    terminals: list[TerminalInspection] = field(default_factory=list)
    cycle_id: str = ""
    capture_id: str = ""
    frame_id: str = ""
    frame_sequence: int = 0
    captured_at_utc: str = ""
    camera_frame_id: str = ""
    camera_timestamp_raw: int | None = None
    frame_width: int = 0
    frame_height: int = 0
    frame_channels: int = 0
    camera_backend: str = ""
    camera_description: str = ""
    evidence_directory: str = ""
    manifest_path: str = ""
    analysis_ready: bool = False
    readiness_issues: list[str] = field(default_factory=list)
    locator_status: str = ""
    classifier_status: str = ""
    aligned_battery_path: str = ""
    reference_battery_path: str = ""
    battery_polygon: list[tuple[float, float]] = field(default_factory=list)
    locator_metrics: dict[str, Any] = field(default_factory=dict)
    # Runtime-only imagery. Production PASS cycles are rendered from these
    # buffers and never receive filesystem paths or a database record.
    full_image: Any | None = field(default=None, repr=False, compare=False)
    aligned_battery_image: Any | None = field(default=None, repr=False, compare=False)
    reference_battery_image: Any | None = field(default=None, repr=False, compare=False)

    @classmethod
    def create(
        cls,
        *,
        recipe: Recipe | None,
        disposition: InspectionDisposition,
        reason: str,
        duration_ms: int,
        trigger_source: str,
        image_quality: str,
        full_image_path: str,
        terminals: list[TerminalInspection],
        cycle_id: str = "",
        capture_id: str = "",
        frame_id: str = "",
        frame_sequence: int = 0,
        captured_at_utc: str = "",
        camera_frame_id: str = "",
        camera_timestamp_raw: int | None = None,
        frame_width: int = 0,
        frame_height: int = 0,
        frame_channels: int = 0,
        camera_backend: str = "",
        camera_description: str = "",
        evidence_directory: str = "",
        manifest_path: str = "",
        inspection_id: str | None = None,
        analysis_ready: bool = False,
        readiness_issues: list[str] | None = None,
        locator_status: str = "",
        classifier_status: str = "",
        aligned_battery_path: str = "",
        reference_battery_path: str = "",
        battery_polygon: list[tuple[float, float]] | None = None,
        locator_metrics: dict[str, Any] | None = None,
        full_image: Any | None = None,
        aligned_battery_image: Any | None = None,
        reference_battery_image: Any | None = None,
    ) -> "InspectionResult":
        return cls(
            inspection_id=inspection_id or str(uuid4()),
            recipe_id=recipe.recipe_id if recipe is not None else "",
            recipe_name=recipe.name if recipe is not None else "NO ACTIVE RECIPE",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            disposition=disposition,
            reason=reason,
            duration_ms=duration_ms,
            trigger_source=trigger_source,
            image_quality=image_quality,
            full_image_path=full_image_path,
            battery_roi=(
                recipe.battery_roi
                if recipe is not None
                else NormalizedRect(0.0, 0.0, 1.0, 1.0)
            ),
            terminals=terminals,
            cycle_id=cycle_id,
            capture_id=capture_id,
            frame_id=frame_id,
            frame_sequence=frame_sequence,
            captured_at_utc=captured_at_utc,
            camera_frame_id=camera_frame_id,
            camera_timestamp_raw=camera_timestamp_raw,
            frame_width=frame_width,
            frame_height=frame_height,
            frame_channels=frame_channels,
            camera_backend=camera_backend,
            camera_description=camera_description,
            evidence_directory=evidence_directory,
            manifest_path=manifest_path,
            analysis_ready=analysis_ready,
            readiness_issues=list(readiness_issues or []),
            locator_status=locator_status,
            classifier_status=classifier_status,
            aligned_battery_path=aligned_battery_path,
            reference_battery_path=reference_battery_path,
            battery_polygon=list(battery_polygon or []),
            locator_metrics=dict(locator_metrics or {}),
            full_image=full_image,
            aligned_battery_image=aligned_battery_image,
            reference_battery_image=reference_battery_image,
        )

    @property
    def passed(self) -> bool:
        return self.disposition == InspectionDisposition.PASS

    @property
    def is_product_result(self) -> bool:
        return bool(
            self.disposition
            in {
                InspectionDisposition.PASS,
                InspectionDisposition.REJECT,
            }
            and self.analysis_ready
            and self.frame_id
            and (self.full_image is not None or bool(self.full_image_path))
        )

    @property
    def result_label(self) -> str:
        return self.disposition.display

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_schema_version": RECORD_SCHEMA_VERSION,
            "inspection_id": self.inspection_id,
            "recipe_id": self.recipe_id,
            "recipe_name": self.recipe_name,
            "timestamp_utc": self.timestamp_utc,
            "disposition": self.disposition.value,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
            "trigger_source": self.trigger_source,
            "image_quality": self.image_quality,
            "full_image_path": self.full_image_path,
            "battery_roi": self.battery_roi.to_dict(),
            "terminals": [terminal.to_dict() for terminal in self.terminals],
            "cycle_id": self.cycle_id,
            "capture_id": self.capture_id,
            "frame_id": self.frame_id,
            "frame_sequence": self.frame_sequence,
            "captured_at_utc": self.captured_at_utc,
            "camera_frame_id": self.camera_frame_id,
            "camera_timestamp_raw": self.camera_timestamp_raw,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "frame_channels": self.frame_channels,
            "camera_backend": self.camera_backend,
            "camera_description": self.camera_description,
            "evidence_directory": self.evidence_directory,
            "manifest_path": self.manifest_path,
            "analysis_ready": self.analysis_ready,
            "readiness_issues": list(self.readiness_issues),
            "locator_status": self.locator_status,
            "classifier_status": self.classifier_status,
            "aligned_battery_path": self.aligned_battery_path,
            "reference_battery_path": self.reference_battery_path,
            "battery_polygon": [list(point) for point in self.battery_polygon],
            "locator_metrics": dict(self.locator_metrics),
        }
