from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any
from uuid import uuid4

import cv2

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal

from battery_inspector.activity import ActivityTracker
from battery_inspector.config import AppConfig, CameraConfig, merge_config
from battery_inspector.data import RecipeRepository
from battery_inspector.data.repository import REVIEW_REVIEWED
from battery_inspector.evidence import (
    prune_staged_captures,
    reference_capture_from_file,
    remove_failure_evidence,
    FailureRetentionPolicy,
    persist_recipe_reference,
    persist_recipe_validation_records,
    stage_reference_capture,
)
from battery_inspector.ml_training import (
    CIRCLE_ROI_SHAPE,
    MlTrainingParameters,
    MlTrainingStore,
    training_environment,
    train_classifier,
)
from battery_inspector.models import (
    InspectionCycleState,
    InspectionCycleStatus,
    InspectionDisposition,
    InspectionResult,
    MarkingClassifierSettings,
    NormalizedRect,
    Recipe,
    RecipeStatus,
    ReferenceCapture,
)
from battery_inspector.paths import disk_health
from battery_inspector.roi_geometry import TAUGHT_CIRCLE_CROP_CONTRACT
from battery_inspector.services import (
    AllenBradleyPlcService,
    BaslerCameraService,
    CameraCapabilities,
    CameraDeviceInfo,
    CameraError,
    CameraFrame,
    InspectionPipeline,
    MockCameraService,
    MockPlcService,
    OnnxPolarityModel,
    TriggerEdgeLatch,
)
from battery_inspector.services.workers import ServiceTask
from battery_inspector.package_transfer import (
    export_failure_package,
    export_model_package,
    export_recipe_package,
    import_model_package,
    import_recipe_package,
    inspect_recipe_package,
)
from battery_inspector.station_transfer import (
    create_station_backup,
    stage_station_restore,
)


# Lighting has no measurement path on this station. The indicator says so
# rather than reporting a reassuring "OK" it cannot substantiate; a fault
# state would be equally untrue, since nothing has detected a lighting fault.
LIGHTING_HEALTH_UNMONITORED = {"ok": True, "text": "NOT MONITORED"}
# Copied at each use site: the health dictionary is handed to the HMI, which
# must not be able to reach back and mutate a module-level default.


class AppController(QObject):
    inspection_updated = Signal(object)
    recipes_changed = Signal(object)
    # Retained failures, or their triage state, changed. The review page reloads.
    failures_changed = Signal()
    active_recipe_changed = Signal(object)
    health_changed = Signal(object)
    counts_changed = Signal(object)
    event_added = Signal(object)
    busy_changed = Signal(bool)
    configuration_changed = Signal(object)
    camera_discovery_changed = Signal(object)
    camera_capabilities_changed = Signal(object)
    camera_test_completed = Signal(object)
    camera_preview_frame = Signal(object)
    camera_preview_state = Signal(bool, str)
    camera_operation_failed = Signal(str)
    camera_operation_busy = Signal(bool)
    plc_test_completed = Signal(object)
    plc_operation_failed = Signal(str)
    plc_operation_busy = Signal(bool)
    plc_simulation_state_changed = Signal(object)
    bypass_operation_failed = Signal(str)
    bypass_operation_busy = Signal(bool)
    camera_operation_queued = Signal(str)
    cycle_state_changed = Signal(object)
    reference_capture_completed = Signal(object)
    reference_capture_failed = Signal(str)
    reference_capture_busy = Signal(bool)
    recipe_validation_completed = Signal(object)
    recipe_validation_failed = Signal(str)
    recipe_validation_busy = Signal(bool)
    ml_model_changed = Signal(object)
    ml_training_capture_completed = Signal(object)
    ml_training_capture_failed = Signal(str)
    ml_training_capture_busy = Signal(bool)
    ml_training_progress = Signal(object)
    ml_training_completed = Signal(object)
    ml_training_failed = Signal(str)
    ml_training_busy = Signal(bool)
    ml_training_samples_changed = Signal(object)
    station_transfer_completed = Signal(object)
    station_transfer_failed = Signal(str)
    station_transfer_busy = Signal(bool)

    def __init__(
        self,
        project_root: Path,
        config: AppConfig,
        *,
        resource_root: Path | None = None,
    ) -> None:
        super().__init__()
        # project_root remains the writable station root for compatibility with
        # backup/restore and relative model paths. Frozen builds use a separate
        # read-only resource root under Program Files/PyInstaller.
        self.project_root = Path(project_root)
        self.resource_root = Path(resource_root or project_root)
        self.assets_directory = self.resource_root / "battery_inspector" / "assets"
        self.config_path = self.project_root / "config.json"
        self.config = config.normalized()
        self.data_directory = self.config.resolved_data_directory(project_root)
        self.repository = RecipeRepository(self.data_directory / "battery_inspector.db")
        self.repository.purge_passing_history()
        self.ml_training_store = MlTrainingStore(self.data_directory / "ml_training")
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(max(2, self.thread_pool.maxThreadCount()))

        self.active_recipe: Recipe | None = self.repository.get_active_recipe()
        self.camera_backend_active = "starting"
        self.camera_fallback_reason = ""
        self.camera = self._build_camera_service()
        self.plc_backend_active = self.config.plc_backend
        self.plc = self._build_plc_service()
        self.pipeline = self._build_inspection_pipeline()
        self.last_inspection: InspectionResult | None = None
        # Yield counters are deliberately session-only. Persisting aggregate
        # PASS history would recreate the production data the station is
        # configured not to retain.
        self.part_count = 0
        self.pass_count = 0
        self.fail_count = 0
        self.recent_results: deque[bool] = deque(maxlen=13)
        self.health: dict[str, dict[str, Any]] = {
            "camera": {"ok": False, "text": "CONNECTING"},
            "lighting": dict(LIGHTING_HEALTH_UNMONITORED),
            "plc": {"ok": False, "text": "CONNECTING"},
            "disk": disk_health(self.data_directory),
            "vision": {"ok": False, "text": "NOT READY", "issues": []},
            "system": {"ok": False, "text": "STARTING"},
        }
        self._activity = ActivityTracker()
        self._activity_lock = RLock()
        self._busy = False
        self._busy_reason = ""
        self._startup_in_flight = False
        self._inspection_in_flight = False
        self._pending_inspection_trigger_source: str | None = None
        self._camera_operation_in_flight = False
        self._camera_apply_task_started = False
        self._pending_camera_settings: CameraConfig | None = None
        # Live camera preview. While it runs, the camera may be carrying
        # settings a technician is still tuning, so it holds the settings to put
        # back and the inspection path treats the camera as occupied.
        self._camera_preview_active = False
        self._camera_preview_in_flight = False
        self._camera_preview_dirty = False
        self._camera_preview_saved: CameraConfig | None = None
        # Last value written to the PLC readiness tag. None means nothing has
        # been written yet, so the first evaluation always publishes.
        self._plc_ready_published: bool | None = None
        self._pending_configuration: AppConfig | None = None
        self._plc_operation_in_flight = False
        self._plc_apply_task_started = False
        self._pending_plc_configuration: AppConfig | None = None
        self._plc_poll_in_flight = False
        self._last_plc_recipe_mismatch = ""
        self._plc_trigger_edge = TriggerEdgeLatch()
        # The acknowledge handshake. The edge latch matters as much here as it
        # does for the trigger: an acknowledge bit left high by a stopped or
        # faulted controller would otherwise clear every future result the
        # instant it was published, and the PLC would see nothing at all.
        self._plc_acknowledge_edge = TriggerEdgeLatch()
        self._plc_result_outstanding = False
        self._plc_unacknowledged_reported = False
        self._last_plc_state: dict[str, Any] = {
            "trigger": False,
            "recipe_name": self.active_recipe.name if self.active_recipe else "",
            "recipe_number": self.active_recipe.recipe_number if self.active_recipe else None,
            "recipe_selector": self.config.plc_recipe_selector,
            "heartbeat": False,
            "bypass": False,
            "passed": None,
            "fail": False,
            "busy": False,
            "complete": False,
        }
        self._bypass_active = False
        self._bypass_known = False
        self._bypass_operation_in_flight = False
        self._plc_heartbeat_in_flight = False
        self._heartbeat_value = False
        self._heartbeat_write_count = 0
        self._heartbeat_last_ok = ""
        self._heartbeat_fault_latched = False
        self._cycle_lock = RLock()
        self._cycle_sequence = 0
        self._cycle_status = InspectionCycleStatus.idle()
        self._reference_capture_in_flight = False
        self._recipe_validation_in_flight = False
        self._ml_training_capture_in_flight = False
        self._ml_training_in_flight = False
        self._station_transfer_in_flight = False
        # True from the moment a recipe is opened for editing or training until
        # it is closed. A technician is at the fixture with parts in their hand
        # for all of that time, not just while a capture is running.
        self._recipe_session_active = False
        # One log line per session about a controller that triggered anyway,
        # rather than one per poll.
        self._recipe_session_trigger_reported = False
        self.plc_poll_timer = QTimer(self)
        self.plc_poll_timer.setInterval(self.config.plc_poll_ms)
        self.plc_poll_timer.timeout.connect(self._poll_plc)
        self.plc_heartbeat_timer = QTimer(self)
        self.plc_heartbeat_timer.setInterval(self.config.plc_heartbeat_ms)
        self.plc_heartbeat_timer.timeout.connect(self._heartbeat_tick)
        self.camera_preview_timer = QTimer(self)
        self.camera_preview_timer.setInterval(self.CAMERA_PREVIEW_INTERVAL_MS)
        self.camera_preview_timer.timeout.connect(self._camera_preview_tick)
        # Automatic reconnection after a lost PLC connection. Single-shot and
        # rescheduled with backoff, so a controller that is down for an hour
        # costs one attempt every PLC_RECONNECT_MAX_MS rather than a tight loop.
        self.plc_reconnect_timer = QTimer(self)
        self.plc_reconnect_timer.setSingleShot(True)
        self.plc_reconnect_timer.timeout.connect(self._attempt_plc_reconnect)
        self._plc_reconnect_delay_ms = 0
        self._plc_reconnect_attempts = 0
        self._plc_reconnect_in_flight = False
        self._plc_reconnect_reported = False

    def create_workstation_backup(self, destination: Path) -> bool:
        """Create a portable station ZIP without blocking the HMI thread."""

        if self._station_transfer_in_flight or self.busy:
            return False
        self._station_transfer_in_flight = True
        self.station_transfer_busy.emit(True)
        self._begin_activity("station_transfer", "CREATING WORKSTATION BACKUP")
        task = ServiceTask(
            create_station_backup,
            self.project_root,
            self.config_path,
            self.data_directory,
            Path(destination),
        )
        task.signals.completed.connect(self._station_backup_completed)
        task.signals.failed.connect(self._station_transfer_failed)
        task.signals.finished.connect(self._station_transfer_finished)
        self.thread_pool.start(task)
        return True

    def stage_workstation_restore(self, source: Path) -> bool:
        """Verify an imported station ZIP and schedule it for the next startup."""

        if self._station_transfer_in_flight or self.busy:
            return False
        self._station_transfer_in_flight = True
        self.station_transfer_busy.emit(True)
        self._begin_activity("station_transfer", "VERIFYING WORKSTATION RESTORE")
        task = ServiceTask(stage_station_restore, self.project_root, Path(source))
        task.signals.completed.connect(self._station_restore_staged)
        task.signals.failed.connect(self._station_transfer_failed)
        task.signals.finished.connect(self._station_transfer_finished)
        self.thread_pool.start(task)
        return True

    def _station_backup_completed(self, payload: object) -> None:
        result = {**dict(payload), "operation": "backup"}  # type: ignore[arg-type]
        self._add_event(
            "BACKUP",
            "Workstation backup created",
            details={
                "path": result.get("path", ""),
                "file_count": result.get("file_count", 0),
                "sha256": result.get("sha256", ""),
            },
        )
        self.station_transfer_completed.emit(result)

    def _station_restore_staged(self, payload: object) -> None:
        result = {**dict(payload), "operation": "restore"}  # type: ignore[arg-type]
        self._add_event(
            "RESTORE",
            "Workstation restore ZIP verified and staged for restart",
            details={
                "path": result.get("path", ""),
                "file_count": result.get("file_count", 0),
                "application_version": result.get("application_version", ""),
            },
        )
        self.station_transfer_completed.emit(result)

    def _station_transfer_failed(self, message: str) -> None:
        self._add_event("BACKUP_RESTORE", f"Workstation transfer failed: {message}")
        self.station_transfer_failed.emit(message)

    def _station_transfer_finished(self) -> None:
        self._station_transfer_in_flight = False
        self.station_transfer_busy.emit(False)
        self._end_activity("station_transfer")

    def _build_camera_service(self):
        if self.config.camera_backend == "simulation":
            self.camera_backend_active = "simulation"
            return MockCameraService(self.assets_directory / "demo_battery.jpg", self.config.camera)
        self.camera_backend_active = "basler"
        return BaslerCameraService(self.config.camera)

    def _build_plc_service(self, configuration: AppConfig | None = None):
        config = (configuration or self.config).normalized()
        recipe_name = self.active_recipe.name if self.active_recipe else "NO_RECIPE"
        recipe_number = self.active_recipe.recipe_number if self.active_recipe else 0
        if config.plc_backend == "simulation":
            return MockPlcService(
                recipe_name=recipe_name,
                recipe_number=recipe_number,
                recipe_selector=config.plc_recipe_selector,
            )
        return AllenBradleyPlcService(
            config.plc_address,
            config.tags,
            config.plc_recipe_selector,
        )

    def _resolve_project_path(self, value: str) -> Path:
        path = Path(str(value or "").strip()).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        return path

    def _build_inspection_pipeline(
        self,
        configuration: AppConfig | None = None,
    ) -> InspectionPipeline:
        config = (configuration or self.config).normalized()
        model = OnnxPolarityModel(
            self._resolve_project_path(config.ml.model_path),
            self._resolve_project_path(config.ml.manifest_path),
        )
        return InspectionPipeline(
            output_directory=self.data_directory,
            ml_model=model,
            failure_retention_policy=self._failure_retention_policy(config),
        )

    @staticmethod
    def _failure_retention_policy(config: AppConfig) -> FailureRetentionPolicy:
        return FailureRetentionPolicy(
            max_age_days=config.failure_retention_days,
            max_bytes=int(config.failure_retention_max_gb * 1024**3),
        )

    def ml_model_info(self, *, require_runtime: bool = False) -> dict[str, Any]:
        info = self.pipeline.ml_model_info(require_runtime=require_runtime)
        info["use_for_new_revisions"] = self.config.ml.use_for_new_revisions
        return info

    def ml_classifier_settings_for_revision(
        self,
        base: MarkingClassifierSettings | None = None,
    ) -> MarkingClassifierSettings:
        """Bind a new recipe revision to the exact active station ML model."""

        current = MarkingClassifierSettings.from_dict(
            (base or MarkingClassifierSettings()).to_dict()
        )
        info = self.ml_model_info(require_runtime=True)
        if not bool(info.get("ready")):
            raise ValueError(
                "The station ML model is not ready: "
                + "; ".join(str(item) for item in info.get("issues", []))
            )
        if str(info.get("input_crop_contract", "")) != TAUGHT_CIRCLE_CROP_CONTRACT:
            raise ValueError(
                "Only the v0.17 taught-circle ML input contract may be bound to new recipe revisions."
            )
        classes = {str(item).strip().lower() for item in list(info.get("classes") or [])}
        if classes != {"plus", "minus", "blank", "invalid_marking"}:
            raise ValueError(
                "Only a four-class PLUS/MINUS/BLANK/INVALID MARKING model may be "
                "bound to new recipe revisions."
            )
        payload = current.to_dict()
        payload.update(
            {
                "method": "onnx_ml",
                "ml_model_id": str(info.get("model_id", "")),
                "ml_model_version": str(info.get("model_version", "")),
                "ml_model_sha256": str(info.get("model_sha256", "")),
            }
        )
        if str(info.get("input_crop_contract", "")) == TAUGHT_CIRCLE_CROP_CONTRACT:
            # Circle-contract models are trained with full rotational
            # augmentation and use the exact observed crop at inference. Keep
            # TTA off unless an engineer explicitly qualifies a future model
            # with another inference contract.
            payload["ml_test_time_quadrants"] = False
        return MarkingClassifierSettings.from_dict(payload)

    def apply_ml_configuration(
        self,
        *,
        model_path: str,
        manifest_path: str,
        use_for_new_revisions: bool,
    ) -> dict[str, Any]:
        """Validate and apply a station ONNX model package while idle.

        Invalid packages are not saved.  Existing active recipes remain bound
        to their prior model hash and will fail closed if an engineer replaces
        the model until a new recipe revision is guided/validated.
        """

        if self._inspection_in_flight or self._recipe_validation_in_flight:
            raise ValueError(
                "ML model changes are blocked only while an inspection or recipe "
                "validation cycle is actively using the vision pipeline."
            )

        proposed = merge_config(
            self.config,
            {
                "ml": {
                    "model_path": model_path,
                    "manifest_path": manifest_path,
                    "use_for_new_revisions": bool(use_for_new_revisions),
                }
            },
        )
        candidate = self._build_inspection_pipeline(proposed)
        info = candidate.ml_model_info(require_runtime=True)
        active_requires_ml = bool(
            self.active_recipe
            and self.active_recipe.classifier_settings.normalized().method == "onnx_ml"
        )
        require_ready = bool(use_for_new_revisions) or active_requires_ml
        if require_ready and not bool(info.get("ready")):
            raise ValueError(
                "ML model package could not be loaded: "
                + "; ".join(str(item) for item in info.get("issues", []))
            )
        self.config = proposed
        self.pipeline = candidate
        self.config.save(self.config_path)
        self._recalculate_system_health()
        self.configuration_changed.emit(self.config)
        self.health_changed.emit(self.health)
        info["use_for_new_revisions"] = self.config.ml.use_for_new_revisions
        self.ml_model_changed.emit(info)
        self._add_event(
            "VISION",
            (
                "Loaded ML polarity model "
                f"{info.get('model_id', '')} {info.get('model_version', '')} "
                f"({str(info.get('model_sha256', ''))[:12]})."
                if info.get("ready")
                else "ML classifier default disabled; no verified model package is active."
            ),
        )
        return info

    def ml_training_summary(self) -> dict[str, Any]:
        return self.ml_training_store.dataset_readiness()

    def ml_training_latest_result(self) -> dict[str, Any] | None:
        return self.ml_training_store.latest_training_result()

    def ml_training_environment_info(self) -> dict[str, Any]:
        return training_environment()

    def ml_training_latest_sample(self, label: str) -> dict[str, Any] | None:
        items = self.ml_training_store.latest(label, 1)
        return items[0].to_dict() if items else None

    def ml_training_samples(self) -> list[dict[str, Any]]:
        return self.ml_training_store.sample_catalog()

    def relabel_ml_training_sample(self, sample_id: str, label: str) -> dict[str, Any]:
        result = self.ml_training_store.relabel_sample(sample_id, label)
        summary = self.ml_training_summary()
        self.ml_training_samples_changed.emit(summary)
        self._add_event(
            "ML_TRAINING",
            f"Relabeled training sample {sample_id} as {label.upper()}",
            details={"sample_id": sample_id, "label": label, **dict(result)},
        )
        return result

    def verify_ml_training_candidate(self, training_result: dict[str, Any]) -> dict[str, Any]:
        from battery_inspector.services.ml import OnnxPolarityModel

        model_path = Path(str(training_result.get("model_path", "")))
        manifest_path = Path(str(training_result.get("manifest_path", "")))
        if not model_path.is_file() or not manifest_path.is_file():
            return {
                "ready": False,
                "issues": ["CANDIDATE_MODEL_PACKAGE_MISSING"],
                "model_path": str(model_path),
                "manifest_path": str(manifest_path),
            }
        return OnnxPolarityModel(model_path, manifest_path).info(require_runtime=True)

    def capture_ml_training_frame(self) -> bool:
        """Acquire a fresh full-resolution frame for guided ML sample capture."""

        if (
            self._ml_training_capture_in_flight
            or self._ml_training_in_flight
            or self._reference_capture_in_flight
            or self._recipe_validation_in_flight
            or self._camera_operation_in_flight
            or self._startup_in_flight
            or self._inspection_in_flight
        ):
            return False
        self._ml_training_capture_in_flight = True
        self.ml_training_capture_busy.emit(True)
        self._begin_activity("camera", "CAPTURING ML TRAINING FRAME")
        task = ServiceTask(self._capture_ml_training_frame)
        task.signals.completed.connect(self._ml_training_capture_complete)
        task.signals.failed.connect(self._ml_training_capture_failed)
        task.signals.finished.connect(self._ml_training_capture_finished)
        self.thread_pool.start(task)
        return True

    def _capture_ml_training_frame(self) -> ReferenceCapture:
        if not self.camera.connected:
            raise CameraError("Camera is not connected; no ML training frame was captured")
        if isinstance(self.camera, MockCameraService):
            raise CameraError(
                "Guided ML training samples require a physical camera. Demo Image and automatic camera fallback are not accepted as training evidence."
            )
        frame = self.camera.capture()
        if not frame.fresh:
            raise CameraError("Camera did not return a fresh frame for ML training")
        requested = getattr(self.camera, "settings", self.config.camera).normalized()
        camera_profile: dict[str, Any] = {"requested": asdict(requested)}
        try:
            state = self.camera.state()
            camera_profile["effective"] = asdict(state.settings)
            camera_profile["capabilities"] = state.capabilities.to_dict()
        except Exception as exc:  # noqa: BLE001
            camera_profile["effective"] = asdict(requested)
            camera_profile["profile_probe_warning"] = str(exc)
        return stage_reference_capture(
            frame,
            self.ml_training_store.staging_root,
            source="ML_TRAINING_WIZARD",
            camera_profile=camera_profile,
        )

    def _ml_training_capture_complete(self, payload: object) -> None:
        if not isinstance(payload, ReferenceCapture):
            self._ml_training_capture_failed("Camera worker returned invalid ML capture metadata")
            return
        self.ml_training_capture_completed.emit(payload)
        self._add_event(
            "ML_TRAINING",
            "Fresh ML training frame captured",
            details={
                "capture_id": payload.capture_id,
                "frame_id": payload.frame_id,
                "resolution": [payload.width_px, payload.height_px],
                "quality": payload.quality,
            },
        )

    def _ml_training_capture_failed(self, message: str) -> None:
        self.ml_training_capture_failed.emit(message)
        self._add_event("ML_TRAINING", f"ML training capture failed: {message}")

    def _ml_training_capture_finished(self) -> None:
        self._ml_training_capture_in_flight = False
        self.ml_training_capture_busy.emit(False)
        self._end_activity("camera")
        self._resume_queued_work()

    def save_ml_training_samples(
        self,
        capture: ReferenceCapture,
        items: list[tuple[str, NormalizedRect, str]],
        *,
        collection_tag: str = "",
        roi_shape: str = "circle",
    ) -> dict[str, Any]:
        results = self.ml_training_store.save_samples(
            capture,
            items,
            collection_tag=collection_tag,
            roi_shape=roi_shape,
        )
        summary = self.ml_training_summary()
        saved_samples: list[dict[str, Any]] = []
        new_count = 0
        duplicate_count = 0
        for record, duplicate in results:
            payload = record.to_dict()
            payload["duplicate"] = bool(duplicate)
            saved_samples.append(payload)
            if duplicate:
                duplicate_count += 1
            else:
                new_count += 1
        summary["saved_samples"] = saved_samples
        summary["batch"] = {
            "capture_id": capture.capture_id,
            "roi_count": len(items),
            "new_count": new_count,
            "duplicate_count": duplicate_count,
            "collection_tag": str(collection_tag or "").strip(),
            "roi_shape": str(roi_shape or "rectangle"),
        }
        self.ml_training_samples_changed.emit(summary)
        self._add_event(
            "ML_TRAINING",
            f"Saved ML capture batch: {new_count} new, {duplicate_count} duplicate",
            details={
                "capture_id": capture.capture_id,
                "collection_tag": str(collection_tag or "").strip(),
                "samples": [
                    {
                        "sample_id": item.get("sample_id", ""),
                        "label": item.get("label", ""),
                        "roi_key": item.get("roi_key", ""),
                        "duplicate": item.get("duplicate", False),
                    }
                    for item in saved_samples
                ],
            },
        )
        return summary

    def save_ml_training_sample(
        self,
        capture: ReferenceCapture,
        roi: NormalizedRect,
        label: str,
        *,
        roi_shape: str = "circle",
    ) -> dict[str, Any]:
        summary = self.save_ml_training_samples(
            capture,
            [("ml_top", roi, label)],
            roi_shape=roi_shape,
        )
        items = list(summary.get("saved_samples") or [])
        if items:
            summary["saved_sample"] = items[0]
        return summary

    def remove_ml_training_sample(self, sample_id: str) -> bool:
        removed = self.ml_training_store.remove_sample(sample_id)
        if removed:
            self.ml_training_samples_changed.emit(self.ml_training_summary())
            self._add_event("ML_TRAINING", f"Removed mislabeled training sample {sample_id}")
        return removed

    def prepare_ml_training_dataset(
        self,
        *,
        validation_fraction: float = 0.15,
        test_fraction: float = 0.15,
    ) -> dict[str, Any]:
        summary = self.ml_training_store.prepare_dataset(
            validation_fraction=validation_fraction,
            test_fraction=test_fraction,
            clean=True,
        )
        self._add_event(
            "ML_TRAINING",
            "Prepared ML train/validation/test dataset",
            details=summary,
        )
        return summary

    def start_ml_training(
        self,
        parameters: MlTrainingParameters,
        dataset_directory: str | Path | None = None,
    ) -> bool:
        if (
            self._ml_training_in_flight
            or self._ml_training_capture_in_flight
            or self._inspection_in_flight
            or self._recipe_validation_in_flight
            or self._reference_capture_in_flight
        ):
            return False
        dataset_path = (
            Path(dataset_directory)
            if dataset_directory is not None
            else self.ml_training_store.datasets_root / "current"
        )
        if not (dataset_path / "summary.json").is_file():
            raise ValueError("Prepare the guided ML dataset before starting training")
        self._ml_training_in_flight = True
        self.ml_training_busy.emit(True)
        self._begin_activity("ml_training", "TRAINING ML MODEL")
        task = ServiceTask(self._run_ml_training, dataset_path, parameters.normalized())
        task.signals.completed.connect(self._ml_training_complete)
        task.signals.failed.connect(self._ml_training_failed)
        task.signals.finished.connect(self._ml_training_finished)
        self.thread_pool.start(task)
        return True

    def _run_ml_training(
        self,
        dataset_path: Path,
        parameters: MlTrainingParameters,
    ) -> dict[str, Any]:
        return train_classifier(
            dataset_path,
            self.ml_training_store.runs_root,
            parameters,
            progress=lambda payload: self.ml_training_progress.emit(payload),
        )

    def _ml_training_complete(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        self.ml_training_completed.emit(result)
        evaluation = dict(result.get("evaluation") or {})
        self._add_event(
            "ML_TRAINING",
            "ML training and held-out evaluation completed",
            details={
                "run_id": result.get("run_id", ""),
                "model_sha256": result.get("model_sha256", ""),
                "acceptance_rate": evaluation.get("acceptance_rate", 0.0),
                "accepted_accuracy": evaluation.get("accepted_accuracy", 0.0),
                "accuracy_with_abstentions": evaluation.get("accuracy_with_abstentions", 0.0),
            },
        )

    def _ml_training_failed(self, message: str) -> None:
        self.ml_training_failed.emit(message)
        self._add_event("ML_TRAINING", f"ML training failed: {message}")

    def _ml_training_finished(self) -> None:
        self._ml_training_in_flight = False
        self.ml_training_busy.emit(False)
        self._end_activity("ml_training")
        self._resume_queued_work()

    def install_ml_training_candidate(
        self,
        training_result: dict[str, Any],
        *,
        use_for_new_revisions: bool = True,
    ) -> dict[str, Any]:
        if self._ml_training_in_flight:
            raise ValueError("Wait for model training to finish before installing the candidate")
        source_model = Path(str(training_result.get("model_path", "")))
        source_manifest = Path(str(training_result.get("manifest_path", "")))
        if not source_model.is_file() or not source_manifest.is_file():
            raise ValueError("Training result does not contain a valid ONNX model package")
        payload = json.loads(source_manifest.read_text(encoding="utf-8"))
        model_id = str(payload.get("model_id", "polarity-model") or "polarity-model")
        model_version = str(payload.get("model_version", "candidate") or "candidate")

        def safe(value: str) -> str:
            return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)

        destination = self.data_directory / "models" / safe(model_id) / safe(model_version)
        destination.mkdir(parents=True, exist_ok=True)
        target_model = destination / "polarity_classifier.onnx"
        target_manifest = destination / "polarity_classifier.json"
        import shutil

        shutil.copy2(source_model, target_model)
        shutil.copy2(source_manifest, target_manifest)
        info = self.apply_ml_configuration(
            model_path=str(target_model),
            manifest_path=str(target_manifest),
            use_for_new_revisions=use_for_new_revisions,
        )
        self._add_event(
            "ML_TRAINING",
            f"Installed trained ML candidate {model_id} {model_version}",
            details={"model": str(target_model), "manifest": str(target_manifest)},
        )
        return info

    # --- a technician has the station -----------------------------------------

    @property
    def recipe_session_active(self) -> bool:
        return self._recipe_session_active

    def begin_recipe_session(self) -> None:
        """Tell the PLC the station is occupied, for as long as a recipe is open.

        Busy used to mean only "an inspection cycle is running", which left the
        whole of recipe editing and validation invisible on the wire: readiness
        dropped for the fraction of a second a sample was being taken and came
        straight back, so a controller watching Ready AND NOT Busy saw a station
        that looked available between samples. It is not available. Somebody is
        standing at the fixture placing parts by hand.

        So Busy is held high for the entire session and Ready is false
        throughout, giving the controller one unambiguous interlock.
        """

        if self._recipe_session_active:
            return
        self._recipe_session_active = True
        self._recipe_session_trigger_reported = False
        self._publish_plc_ready()
        self._assert_recipe_session_busy()
        self._add_event(
            "RECIPE",
            "Recipe opened for editing; PLC Busy held high until it is closed",
        )

    def end_recipe_session(self) -> None:
        """Release the station. Busy returns low and readiness is republished."""

        if not self._recipe_session_active:
            return
        self._recipe_session_active = False
        if self.plc.connected:
            try:
                self.plc.clear_result()
            except Exception as exc:  # noqa: BLE001
                self._add_event(
                    "PLC",
                    f"Could not clear Busy after the recipe was closed: {exc}",
                )
            else:
                self._plc_result_outstanding = False
                self._emit_plc_simulation_state()
        self._publish_plc_ready()
        self._add_event("RECIPE", "Recipe closed; PLC Busy released")

    def _assert_recipe_session_busy(self) -> None:
        """Write Busy high for an open recipe session.

        Called when the session opens and again whenever the PLC connection is
        re-established, because a controller that dropped and came back has no
        memory of what it was told before.
        """

        if not self._recipe_session_active or not self.plc.connected:
            return
        try:
            self.plc.publish_result(passed=False, busy=True)
        except Exception as exc:  # noqa: BLE001
            self._add_event("PLC", f"Could not hold Busy for the open recipe: {exc}")
            return
        self._emit_plc_simulation_state()

    # --- reviewing what rejected --------------------------------------------

    def list_failures(self, **filters: Any) -> list[dict[str, Any]]:
        """Retained non-PASS records, newest first. See RecipeRepository."""

        return self.repository.list_failures(**filters)

    def failure_counts(self) -> dict[str, int]:
        return self.repository.failure_counts()

    def mark_failures_reviewed(self, inspection_ids: list[str]) -> int:
        changed = self.repository.set_failure_review_state(
            inspection_ids,
            REVIEW_REVIEWED,
            username=self.config.operator_name,
        )
        if changed:
            self._add_event(
                "FAILURE_REVIEW",
                f"Marked {changed} failure(s) reviewed",
                details={"inspection_ids": list(inspection_ids)},
            )
            self.failures_changed.emit()
        return changed

    def set_failures_kept(self, inspection_ids: list[str], keep: bool) -> int:
        """Hold a failure back from retention, or release it.

        A kept record survives the age and capacity passes. The interesting
        failure is usually the one somebody is still working on, and it was
        also the one most likely to age out before they got to it.
        """

        changed = self.repository.set_failure_keep(inspection_ids, keep)
        if changed:
            self._add_event(
                "FAILURE_REVIEW",
                f"{'Held' if keep else 'Released'} {changed} failure(s) "
                f"{'from' if keep else 'to'} evidence retention",
                details={"inspection_ids": list(inspection_ids), "keep": keep},
            )
            self.failures_changed.emit()
        return changed

    def export_failures(
        self,
        records: list[dict[str, Any]],
        destination: Path,
        *,
        description: str = "",
    ) -> dict[str, Any]:
        """Write selected failures, with their evidence, as one checksummed ZIP."""

        result = export_failure_package(
            records=records,
            destination=Path(destination),
            station_name=self.config.operator_name,
            description=description,
        )
        identifiers = [str(item.get("inspection_id", "")) for item in records]
        self.repository.mark_failures_exported([item for item in identifiers if item])
        manifest = result.get("manifest", {})
        self._add_event(
            "FAILURE_REVIEW",
            f"Exported {manifest.get('record_count', 0)} failure(s) for review",
            details={
                "destination": str(result.get("path", "")),
                "evidence_missing": manifest.get("evidence_missing", []),
            },
        )
        self.failures_changed.emit()
        return result

    def send_failure_to_training(
        self,
        record: dict[str, Any],
        labels: dict[str, str],
    ) -> dict[str, Any]:
        """Add a failure's terminal crops to the ML training set.

        ``labels`` maps a terminal key to the class the technician says is
        actually stamped on that terminal. The detected class is never used as a
        default anywhere in this path: a rejected part is exactly the case where
        the model may have been wrong, and defaulting to what it said would
        train it on its own mistakes.

        The crop is taken from the stored full-resolution frame using the
        terminal polygon recorded for that cycle, then re-cropped by the same
        ``ml_input_crop`` contract a live capture uses, so a sample added here
        is indistinguishable from one captured on the ML Training page.
        """

        if self.busy:
            raise ValueError("Wait until the station is idle before adding training samples.")
        payload = dict(record.get("payload") or {})
        frame_path = Path(str(payload.get("full_image_path", "") or ""))
        if not frame_path.is_file():
            raise ValueError(
                "The full-resolution frame for this failure is no longer on the "
                "station, so no training crop can be taken from it."
            )
        terminals = {
            str(item.get("terminal_key", "")): item
            for item in payload.get("terminals", [])
            if isinstance(item, dict)
        }
        items: list[tuple[str, NormalizedRect, str]] = []
        roi_shape = CIRCLE_ROI_SHAPE
        for terminal_key, label in labels.items():
            if not str(label or "").strip():
                continue
            terminal = terminals.get(terminal_key)
            if terminal is None:
                continue
            # The MARKING polygon, not the terminal polygon. The terminal
            # polygon is the locator's search area -- deliberately larger than
            # the post so the terminal can be found inside it -- and a crop of
            # that carries case, background, and often part of the other
            # terminal. The marking polygon is the taught circle on the metal
            # top, which is what the classifier is trained and run on.
            polygon = [tuple(point) for point in terminal.get("marking_polygon", [])]
            if len(polygon) < 3:
                raise ValueError(
                    f"{terminal_key} has no recorded marking outline for this cycle, "
                    "so the training crop cannot be located in the stored frame. "
                    "Capture this sample on the ML Training page instead."
                )
            metrics = dict(terminal.get("classification_metrics") or {})
            roi_shape = str(metrics.get("marking_roi_shape", CIRCLE_ROI_SHAPE) or CIRCLE_ROI_SHAPE)
            xs = [float(point[0]) for point in polygon]
            ys = [float(point[1]) for point in polygon]
            rect = NormalizedRect(
                min(xs),
                min(ys),
                max(1e-3, max(xs) - min(xs)),
                max(1e-3, max(ys) - min(ys)),
            ).clamped()
            items.append((terminal_key, rect, str(label).strip().lower()))

        if not items:
            raise ValueError("Choose the true class for at least one terminal.")

        capture = reference_capture_from_file(
            frame_path,
            source="FAILURE_REVIEW",
            camera_backend=str(payload.get("camera_backend", "")),
            camera_description=str(payload.get("camera_description", "")),
        )
        saved = self.ml_training_store.save_samples(
            capture,
            items,
            collection_tag=f"failure_review:{record.get('inspection_id', '')}",
            roi_shape=roi_shape,
        )
        inspection_id = str(record.get("inspection_id", ""))
        if inspection_id:
            self.repository.mark_failures_trained(
                [inspection_id],
                username=self.config.operator_name,
            )
        added = sum(1 for _sample, duplicate in saved if not duplicate)
        duplicates = len(saved) - added
        self._add_event(
            "ML_TRAINING",
            f"Added {added} training sample(s) from rejected part {inspection_id}"
            + (f" ({duplicates} already present)" if duplicates else ""),
            details={
                "inspection_id": inspection_id,
                "labels": dict(labels),
                "duplicates": duplicates,
            },
        )
        self.failures_changed.emit()
        return {"added": added, "duplicates": duplicates, "samples": saved}

    def clear_failures(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Delete the evidence for the given failures, and their rows.

        Deletion is scoped by ``remove_failure_evidence``, which only removes a
        two-level production cycle directory carrying a non-PASS manifest. A
        path that is not one -- a recipe reference, validation evidence, a model
        -- cannot be removed through here whatever is passed.
        """

        if self.busy:
            raise ValueError("Wait until the station is idle before clearing evidence.")
        directories = [
            str(item.get("evidence_directory", "") or "")
            for item in records
            if str(item.get("evidence_directory", "") or "")
        ]
        summary = remove_failure_evidence(
            self.data_directory / "inspections",
            directories,
        )
        identifiers = [
            str(item.get("inspection_id", ""))
            for item in records
            if str(item.get("inspection_id", ""))
        ]
        summary["rows_removed"] = self.repository.delete_failures(identifiers)
        self._add_event(
            "FAILURE_REVIEW",
            f"Cleared {summary['rows_removed']} failure record(s) and "
            f"{summary['removed']} evidence folder(s), "
            f"reclaiming {summary['bytes_removed'] / 1024 / 1024:.1f} MB",
            details=dict(summary),
        )
        self.failures_changed.emit()
        return summary

    # --- moving one model or one recipe between stations --------------------

    def export_model_package(self, destination: Path) -> dict[str, Any]:
        """Package the model this station is inspecting with."""

        return export_model_package(
            model_path=self._resolve_project_path(self.config.ml.model_path),
            manifest_path=self._resolve_project_path(self.config.ml.manifest_path),
            destination=Path(destination),
            station_name=self.config.operator_name,
        )

    def import_model_package(self, source: Path, *, install: bool = True) -> dict[str, Any]:
        """Verify a model package, lay it down, and make it this station's model.

        Installing does not make any existing recipe production-ready: a recipe
        revision stays bound to the model hash it was validated against, and one
        bound to a different hash keeps failing closed until it is revalidated.
        """

        if self._inspection_in_flight or self._recipe_validation_in_flight:
            raise ValueError(
                "Wait for the current inspection or validation cycle before importing a model."
            )
        result = import_model_package(Path(source), self.data_directory / "models")
        manifest = result["manifest"]
        if install:
            result["info"] = self.apply_ml_configuration(
                model_path=result["model_path"],
                manifest_path=result["manifest_path"],
                use_for_new_revisions=self.config.ml.use_for_new_revisions,
            )
        self._add_event(
            "ML_TRAINING",
            "Imported ML model package "
            f"{manifest.get('model_id', '')} {manifest.get('model_version', '')} "
            f"from station {manifest.get('source_station', '') or 'unnamed'}"
            + ("" if install else " without installing it"),
            details={
                "model_sha256": result.get("model_sha256", ""),
                "source_package": str(source),
                "installed": bool(install),
            },
        )
        return result

    def export_recipe_package(
        self,
        recipe: Recipe,
        destination: Path,
        *,
        include_model: bool = True,
    ) -> dict[str, Any]:
        """Package one recipe revision, with the model it is bound to."""

        model_path: Path | None = None
        manifest_path: Path | None = None
        if include_model:
            binding = recipe.classifier_settings.normalized()
            station_model = self.ml_model_info(require_runtime=False)
            # Only ship the station model when it is the one this revision was
            # validated against. Any other model would travel with the recipe
            # looking like its binding and would not satisfy it.
            if (
                binding.ml_model_sha256
                and binding.ml_model_sha256 == str(station_model.get("model_sha256", ""))
            ):
                model_path = self._resolve_project_path(self.config.ml.model_path)
                manifest_path = self._resolve_project_path(self.config.ml.manifest_path)

        result = export_recipe_package(
            recipe=recipe,
            destination=Path(destination),
            model_path=model_path,
            model_manifest_path=manifest_path,
            station_name=self.config.operator_name,
        )
        self._add_event(
            "RECIPE",
            f"Exported recipe package {recipe.name} revision {recipe.revision}"
            + (" with its bound model" if model_path is not None else " without a model"),
            details={
                "recipe_id": recipe.recipe_id,
                "revision": recipe.revision,
                "destination": str(result.get("path", "")),
                "validation_complete": recipe.validation_complete,
            },
        )
        return result

    def inspect_recipe_package(self, source: Path) -> dict[str, Any]:
        """Read a recipe package's manifest so a technician can decide."""

        manifest = inspect_recipe_package(Path(source))
        binding = str(manifest.get("ml_model_sha256", "") or "")
        station_model = str(self.ml_model_info(require_runtime=False).get("model_sha256", ""))
        manifest = dict(manifest)
        manifest["station_model_sha256"] = station_model
        manifest["model_matches_station"] = bool(binding and binding == station_model)
        return manifest

    def import_recipe_package(self, source: Path, *, install_model: bool = True) -> dict[str, Any]:
        """Bring a packaged recipe onto this station, evidence and all.

        The recipe arrives with the validation evidence the source station
        recorded, which is a deliberate product decision: a package exists to
        move a qualified recipe to a second machine without re-teaching it. The
        evidence was taken on another station's camera and lighting, so the
        import is a technician's decision and both what was imported and where
        it came from are written to the audit log.
        """

        if self.busy:
            raise ValueError("Wait until the station is idle before importing a recipe package.")
        result = import_recipe_package(
            Path(source),
            reference_root=self.data_directory / "recipe_staging",
            models_root=self.data_directory / "models",
        )
        recipe: Recipe = result["recipe"]
        existing = self.repository.get_recipe(recipe.recipe_id, recipe.revision)
        if existing is not None:
            raise ValueError(
                f"{recipe.name} revision {recipe.revision} is already on this station. "
                "A revision is immutable, so an import may not overwrite one; export a "
                "new revision from the source station instead."
            )

        model = result.get("model")
        if install_model and isinstance(model, dict):
            self.apply_ml_configuration(
                model_path=str(model["model_path"]),
                manifest_path=str(model["manifest_path"]),
                use_for_new_revisions=self.config.ml.use_for_new_revisions,
            )

        saved = self.repository.save_recipe(
            recipe,
            username=self.config.operator_name,
            message=f"Stored imported revision {recipe.revision} of {recipe.name}",
        )
        manifest = result["manifest"]
        self._add_event(
            "RECIPE",
            f"Imported recipe package {saved.name} revision {saved.revision} from station "
            f"{manifest.get('source_station', '') or 'unnamed'} "
            f"(validation evidence carried across stations: "
            f"{saved.validation_runs_passed}/{saved.validation_runs_required} samples)",
            details={
                "recipe_id": saved.recipe_id,
                "recipe_number": saved.recipe_number,
                "revision": saved.revision,
                "source_station": manifest.get("source_station", ""),
                "source_created_at_utc": manifest.get("created_at_utc", ""),
                "validation_complete": saved.validation_complete,
                "ml_model_sha256": manifest.get("ml_model_sha256", ""),
                "model_imported": bool(model),
                "source_package": str(source),
            },
        )
        self.recipes_changed.emit(self.list_recipes())
        result["recipe"] = saved
        return result

    def _replace_plc_service(self, replacement, active_backend: str) -> None:
        previous = self.plc
        previous_bypass = self._bypass_active
        self.plc = replacement
        self.plc_backend_active = active_backend
        self._plc_trigger_edge.reset()
        self._plc_acknowledge_edge.reset()
        # The replacement service has never been told anything, so the next
        # evaluation must write regardless of what the old one was told.
        self._plc_ready_published = None
        self._plc_result_outstanding = False
        self._plc_unacknowledged_reported = False
        self._last_plc_state.update(
            {
                "trigger": False,
                "recipe_name": self.active_recipe.name if self.active_recipe else "",
                "recipe_number": self.active_recipe.recipe_number if self.active_recipe else None,
                "recipe_selector": self.config.plc_recipe_selector,
                "heartbeat": False,
                "bypass": False,
                "passed": None,
                "fail": False,
                "busy": False,
                "complete": False,
            }
        )
        self._bypass_active = False
        self._bypass_known = False
        self._heartbeat_value = False
        self._heartbeat_write_count = 0
        self._heartbeat_last_ok = ""
        self._heartbeat_fault_latched = False
        try:
            if previous_bypass and previous.connected:
                previous.set_bypass(False)
        except Exception:  # noqa: S110 - a vanished PLC cannot accept the clear; the watchdog covers it
            # A failed/vanished PLC may not accept the clear. PLC-side effective
            # bypass must therefore also be conditioned on the heartbeat watchdog.
            pass
        try:
            previous.disconnect()
        except Exception:  # noqa: S110 - best-effort teardown of a service already being replaced
            pass

    @property
    def camera_driver_name(self) -> str:
        if isinstance(self.camera, BaslerCameraService):
            if self.camera_backend_active == "basler_defaults":
                return "pypylon — first available device (camera defaults)"
            return "pypylon — first available device"
        if self.camera_backend_active == "simulation_fallback":
            return "MockCameraService — automatic fallback"
        return "MockCameraService"

    @property
    def plc_driver_name(self) -> str:
        return "pycomm3 LogixDriver" if isinstance(self.plc, AllenBradleyPlcService) else "MockPlcService"

    @property
    def plc_simulation_active(self) -> bool:
        return isinstance(self.plc, MockPlcService) and self.plc.connected

    def plc_simulation_state(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = dict(self._last_plc_state)
        snapshot.setdefault("connected", bool(self.plc.connected))
        if isinstance(self.plc, MockPlcService):
            snapshot.update(self.plc.snapshot())
        snapshot.update(
            {
                "connected": bool(self.plc.connected),
                "active": self.plc_simulation_active,
                "configured_backend": self.config.plc_backend,
                "active_backend": self.plc_backend_active,
                "fallback": False,
                "bypass": bool(snapshot.get("bypass", self._bypass_active)),
                "bypass_known": self._bypass_known or (isinstance(self.plc, MockPlcService) and self.plc.connected),
                "bypass_pending": self._bypass_operation_in_flight,
                "heartbeat": bool(snapshot.get("heartbeat", self._heartbeat_value)),
                "heartbeat_count": self._heartbeat_write_count,
                "heartbeat_last_ok": self._heartbeat_last_ok,
                "heartbeat_interval_ms": self.config.plc_heartbeat_ms,
            }
        )
        return snapshot

    @property
    def bypass_active(self) -> bool:
        return self._bypass_active

    def _emit_plc_simulation_state(self) -> None:
        self.plc_simulation_state_changed.emit(self.plc_simulation_state())

    @property
    def reject_rate(self) -> float:
        return (self.fail_count / self.part_count * 100.0) if self.part_count else 0.0

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def busy_reason(self) -> str:
        return self._busy_reason

    @property
    def camera_capabilities(self) -> CameraCapabilities | None:
        return self.camera.capabilities

    @property
    def cycle_status(self) -> InspectionCycleStatus:
        with self._cycle_lock:
            return replace(self._cycle_status)

    def inspection_readiness(self, recipe: Recipe | None = None) -> dict[str, Any]:
        target = recipe if recipe is not None else self.active_recipe
        issues = self.pipeline.readiness_issues(target)
        return {
            "ready": not issues,
            "issues": issues,
            "locator_status": self.pipeline.battery_locator.status,
            "classifier_status": self.pipeline.classifier_status_for_recipe(target),
            "recipe_has_reference": bool(target and target.has_reference_image),
        }

    def _begin_cycle_status(self, trigger_source: str) -> InspectionCycleStatus:
        with self._cycle_lock:
            self._cycle_sequence += 1
            sequence = self._cycle_sequence
            now = datetime.now(timezone.utc)
            self._cycle_status = InspectionCycleStatus(
                cycle_id=f"CYCLE-{now:%Y%m%d-%H%M%S-%f}-{sequence:06d}",
                state=InspectionCycleState.ACQUIRING,
                trigger_source=trigger_source.upper(),
                message="Acquiring a fresh camera frame",
                started_at_utc=now.isoformat(),
                updated_at_utc=now.isoformat(),
            )
            payload = replace(self._cycle_status)
        self.cycle_state_changed.emit(payload)
        return payload

    def _set_cycle_state(
        self,
        state: InspectionCycleState,
        message: str,
        *,
        frame: CameraFrame | None = None,
    ) -> None:
        with self._cycle_lock:
            changes: dict[str, Any] = {}
            if frame is not None:
                changes.update(
                    capture_id=frame.frame_id,
                    frame_id=frame.frame_id,
                    frame_sequence=frame.sequence,
                    captured_at_utc=frame.captured_at_utc,
                )
            self._cycle_status = self._cycle_status.with_state(state, message, **changes)
            payload = replace(self._cycle_status)
        self.cycle_state_changed.emit(payload)

    def initialize(self) -> None:
        self._startup_in_flight = True
        self._begin_activity("startup", "STARTING SERVICES")
        task = ServiceTask(self._connect_and_run_initial)
        task.signals.completed.connect(self._initialization_complete)
        task.signals.failed.connect(self._initialization_failed)
        task.signals.finished.connect(self._initialization_finished)
        self.thread_pool.start(task)

    def _connect_and_run_initial(
        self,
    ) -> tuple[
        InspectionResult | None,
        dict[str, dict[str, Any]],
        list[CameraDeviceInfo],
        CameraCapabilities | None,
    ]:
        camera_error = ""
        plc_error = ""

        # Retention can inspect many evidence files, so run it in the existing
        # startup worker rather than blocking construction of the Qt window.
        self.pipeline.apply_failure_retention(
            self.repository.protected_evidence_directories()
        )
        self._prune_staged_captures()

        try:
            self._connect_configured_camera()
        except Exception as exc:  # noqa: BLE001 - surfaced as station health
            camera_error = str(exc)

        # The configured PLC mode is authoritative. A failed physical PLC stays
        # faulted until the technician repairs it or explicitly selects Simulation.
        try:
            self.plc.connect()
            self.plc.clear_result()
        except Exception as exc:  # noqa: BLE001
            plc_error = str(exc)
            try:
                self.plc.disconnect()
            except Exception:  # noqa: S110 - best-effort teardown after a failed connect; plc_error is reported
                pass
        else:
            self.plc_backend_active = self.config.plc_backend

        devices: list[CameraDeviceInfo] = []
        if isinstance(self.camera, BaslerCameraService) and self.camera.connected:
            try:
                devices = list(self.camera.discover_devices())
            except Exception:
                devices = []

        # Startup never acquires or grades an image. A displayed inspection must
        # always belong to an explicit manual/PLC cycle.
        result: InspectionResult | None = None
        camera_ok = bool(
            self.camera.connected
            and not camera_error
            and self.camera_backend_active not in {"simulation_fallback", "basler_defaults"}
        )
        plc_ok = bool(self.plc.connected)
        readiness = self.pipeline.readiness_issues(self.active_recipe)
        vision_ready = not readiness

        if self.camera_backend_active == "simulation":
            camera_text = "SIMULATION"
        elif self.camera_backend_active == "simulation_fallback":
            camera_text = "SIM FALLBACK"
        elif self.camera_backend_active == "basler_defaults":
            camera_text = "CAM DEFAULTS"
        else:
            camera_text = "OK" if camera_ok else "FAULT"

        if isinstance(self.plc, MockPlcService):
            plc_text = "SIMULATION"
        else:
            plc_text = "OK" if plc_ok else "FAULT"

        system_ok = camera_ok and plc_ok and vision_ready
        if not camera_ok or not plc_ok:
            system_text = "DEGRADED"
        elif not vision_ready:
            system_text = "NOT READY"
        else:
            system_text = "GOOD"

        if camera_error:
            self.repository.add_audit_event(
                username=self.config.operator_name,
                category="CAMERA",
                message=camera_error,
            )
        if plc_error:
            self.repository.add_audit_event(
                username=self.config.operator_name,
                category="PLC",
                message=plc_error,
            )
        return (
            result,
            {
                "camera": {"ok": camera_ok, "text": camera_text},
                "lighting": dict(LIGHTING_HEALTH_UNMONITORED),
                "plc": {"ok": plc_ok, "text": plc_text},
                "disk": disk_health(self.data_directory),
                "vision": {
                    "ok": vision_ready,
                    "text": "READY" if vision_ready else "NOT READY",
                    "issues": readiness,
                },
                "system": {"ok": system_ok, "text": system_text},
            },
            devices,
            self.camera.capabilities,
        )

    def _prune_staged_captures(self) -> None:
        """Drop abandoned reference captures left in the staging directories.

        Each is a full-resolution lossless frame, tens of megabytes, written on
        every capture in the recipe wizard and the ML training page. Accepting
        one copies it into an immutable recipe revision or the sample store, so
        the staged original is redundant from then on, and an abandoned one is
        never referenced again. Nothing removed them, so they accumulated for
        the life of the station.
        """

        summary = prune_staged_captures(
            (
                self.ml_training_store.staging_root,
                self.data_directory / "recipe_staging",
            )
        )
        if summary["removed_count"]:
            self._add_event(
                "SYSTEM",
                f"Removed {summary['removed_count']} abandoned reference capture(s), "
                f"reclaiming {summary['reclaimed_bytes'] / 1024 / 1024:.1f} MB",
                details=summary,
            )

    def _connect_configured_camera(self) -> None:
        """Connect without binding the station to a model or serial number."""

        try:
            self.camera.connect()
            self.camera_backend_active = (
                "basler" if isinstance(self.camera, BaslerCameraService) else "simulation"
            )
            self.camera_fallback_reason = ""
            return
        except Exception as exc:
            if self.config.camera_backend not in {"auto", "basler"}:
                raise

            # A profile saved on another station may request a feature that the
            # newly detected camera does not support. Retry with camera defaults
            # before abandoning physical hardware. This keeps the station
            # capability-driven instead of model-specific.
            safe_profile = CameraConfig(timeout_ms=self.config.camera.timeout_ms)
            replacement = BaslerCameraService(safe_profile)
            safe_cause: Exception | None = None
            try:
                replacement.connect()
            except Exception as safe_exc:
                safe_cause = safe_exc
                replacement.disconnect()
                safe_error = str(safe_exc)
            else:
                try:
                    self.camera.disconnect()
                except Exception:  # noqa: S110 - best-effort teardown of the camera being replaced
                    pass
                self.camera = replacement
                self.camera_backend_active = "basler_defaults"
                self.camera_fallback_reason = (
                    "Saved camera profile could not be applied to this device. "
                    f"Camera defaults are active until settings are verified: {exc}"
                )
                return

            if self.config.camera_backend == "basler":
                raise CameraError(
                    "The first Basler camera could not be opened with either the saved "
                    f"profile or camera defaults. Saved profile: {exc}; defaults: {safe_error}"
                ) from safe_cause

            # If no physical device can be opened, keep commissioning possible with
            # an unmistakably marked simulation fallback. Production must not treat
            # this backend as a verified physical camera.
            self.camera_fallback_reason = f"{exc}; camera-default retry: {safe_error}"
            try:
                self.camera.disconnect()
            except Exception:  # noqa: S110 - best-effort teardown before falling back to simulation
                pass
            self.camera = MockCameraService(
                self.assets_directory / "demo_battery.jpg",
                self.config.camera,
            )
            self.camera.connect()
            self.camera_backend_active = "simulation_fallback"

    def _initialization_complete(self, payload: object) -> None:
        result, health, devices, capabilities = payload  # type: ignore[misc]
        self.health = health
        self.health_changed.emit(self.health)
        self.camera_discovery_changed.emit(
            {
                "devices": devices,
                "capabilities": capabilities,
                "active_backend": self.camera_backend_active,
                "configured_backend": self.config.camera_backend,
            }
        )
        if capabilities is not None:
            self.camera_capabilities_changed.emit(capabilities)
        if result is not None:
            self._accept_inspection(result, increment_counts=False)
        self.recipes_changed.emit(self.list_recipes())
        if self.active_recipe:
            self.active_recipe_changed.emit(self.active_recipe)
        if self.camera_fallback_reason:
            self._add_event("CAMERA", self.camera_fallback_reason)
        self._add_event(
            "SYSTEM",
            "Application services initialized",
            details={
                "camera_backend": self.camera_backend_active,
                "plc_backend": self.config.plc_backend,
            },
        )
        if self.plc.connected and not self.plc_poll_timer.isActive():
            self.plc_poll_timer.start()
        if self.plc.connected and not self.plc_heartbeat_timer.isActive():
            self.plc_heartbeat_timer.start()
        self._emit_plc_simulation_state()
        self.cycle_state_changed.emit(self.cycle_status)
        self._publish_plc_ready(force=True)

    def _initialization_failed(self, message: str) -> None:
        self.health["system"] = {"ok": False, "text": "FAULT"}
        if not self.camera.connected:
            self.health["camera"] = {"ok": False, "text": "FAULT"}
        if not self.plc.connected:
            self.health["plc"] = {"ok": False, "text": "FAULT"}
        self.health_changed.emit(self.health)
        self._add_event("FAULT", message)

    def _initialization_finished(self) -> None:
        self._startup_in_flight = False
        self._end_activity("startup")
        # Only now is the answer meaningful: the PLC is connected and startup no
        # longer holds the camera. Forced, because the controller has been told
        # nothing yet and must not be left inferring readiness from silence.
        self._publish_plc_ready(force=True)
        self._assert_recipe_session_busy()
        self._resume_queued_work()

    def discover_camera_hardware(self) -> bool:
        """Enumerate devices and probe the automatically selected first camera.

        Returns ``False`` when another inspection/camera operation owns the service so
        the HMI can explain why the request was not started instead of failing silently.
        """

        if (
            self._camera_operation_in_flight
            or self._startup_in_flight
            or self._inspection_in_flight
            or self._ml_training_capture_in_flight
        ):
            return False
        self._set_camera_operation_busy(True)
        task = ServiceTask(self._discover_camera_hardware)
        task.signals.completed.connect(self._camera_discovery_complete)
        task.signals.failed.connect(self._camera_discovery_failed)
        task.signals.finished.connect(self._camera_discovery_finished)
        self.thread_pool.start(task)
        return True

    def _discover_camera_hardware(self) -> dict[str, Any]:
        # SCAN PHYSICAL CAMERAS always means physical pylon hardware. It must not enumerate
        # MockCameraService merely because the PLC or camera is being simulated.
        probe_error = ""
        if isinstance(self.camera, BaslerCameraService) and self.camera.connected:
            devices = self.camera.discover_devices()
            try:
                capabilities = self.camera.probe_capabilities() if devices else None
            except Exception as exc:
                capabilities = None
                probe_error = str(exc)
        else:
            probe = BaslerCameraService(self.config.camera)
            try:
                devices = probe.discover_devices()
                try:
                    capabilities = probe.state().capabilities if devices else None
                except Exception as exc:
                    capabilities = None
                    probe_error = str(exc)
            finally:
                probe.disconnect()
        return {
            "devices": devices,
            "capabilities": capabilities,
            "active_backend": self.camera_backend_active,
            "configured_backend": self.config.camera_backend,
            "probe_error": probe_error,
        }

    def _camera_discovery_complete(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        self.camera_discovery_changed.emit(result)
        capabilities = result.get("capabilities")
        if capabilities is not None:
            self.camera_capabilities_changed.emit(capabilities)
        devices = result.get("devices", [])
        self._add_event("CAMERA", f"Camera discovery found {len(devices)} device(s)")

    def _camera_discovery_failed(self, message: str) -> None:
        # A discovery failure is not allowed to mark a working simulation camera as
        # failed. The scan result carries the reason and the HMI remains responsive.
        self.camera_discovery_changed.emit(
            {
                "devices": [],
                "capabilities": None,
                "active_backend": self.camera_backend_active,
                "configured_backend": self.config.camera_backend,
                "error": message,
            }
        )
        self._add_event("CAMERA", f"Physical camera scan failed: {message}")

    def _camera_discovery_finished(self) -> None:
        self._set_camera_operation_busy(False)
        self._resume_queued_work()

    def capture_recipe_reference(self) -> bool:
        """Acquire and stage one fresh image for the recipe wizard."""

        if (
            self._reference_capture_in_flight
            or self._recipe_validation_in_flight
            or self._camera_operation_in_flight
            or self._startup_in_flight
            or self._inspection_in_flight
            or self._ml_training_capture_in_flight
            or self._ml_training_in_flight
        ):
            return False
        self._reference_capture_in_flight = True
        self.reference_capture_busy.emit(True)
        self._begin_activity("camera", "CAPTURING RECIPE REFERENCE")
        task = ServiceTask(self._capture_recipe_reference)
        task.signals.completed.connect(self._reference_capture_complete)
        task.signals.failed.connect(self._reference_capture_failed)
        task.signals.finished.connect(self._reference_capture_finished)
        self.thread_pool.start(task)
        return True

    def _capture_recipe_reference(self) -> ReferenceCapture:
        if not self.camera.connected:
            raise CameraError("Camera is not connected; no reference image was captured")
        if self.camera_backend_active == "simulation_fallback":
            raise CameraError(
                "The physical camera is unavailable and automatic demo-image fallback is active. "
                "Reconnect/apply the camera, or explicitly select Demo Image for offline recipe work."
            )
        frame = self.camera.capture()
        if not frame.fresh:
            raise CameraError("Camera did not return a fresh frame for the reference request")

        requested = getattr(self.camera, "settings", self.config.camera).normalized()
        camera_profile: dict[str, Any] = {"requested": asdict(requested)}
        try:
            state = self.camera.state()
            camera_profile["effective"] = asdict(state.settings)
            camera_profile["capabilities"] = state.capabilities.to_dict()
        except Exception as exc:  # noqa: BLE001 - the acquired frame remains valid evidence
            # A post-capture capability probe is useful metadata, but it must not
            # discard an otherwise valid fresh reference frame.
            camera_profile["effective"] = asdict(requested)
            camera_profile["profile_probe_warning"] = str(exc)

        return stage_reference_capture(
            frame,
            self.data_directory / "recipe_staging",
            source="RECIPE_WIZARD",
            camera_profile=camera_profile,
        )

    def _reference_capture_complete(self, payload: object) -> None:
        if not isinstance(payload, ReferenceCapture):
            self._reference_capture_failed("Camera worker returned invalid reference metadata")
            return
        self.reference_capture_completed.emit(payload)
        self._add_event(
            "CAMERA",
            "Fresh recipe reference image captured",
            details={
                "capture_id": payload.capture_id,
                "frame_id": payload.frame_id,
                "resolution": [payload.width_px, payload.height_px],
                "quality": payload.quality,
            },
        )

    def _reference_capture_failed(self, message: str) -> None:
        self.reference_capture_failed.emit(message)
        self._add_event("CAMERA", f"Recipe reference capture failed: {message}")

    def _reference_capture_finished(self) -> None:
        self._reference_capture_in_flight = False
        self.reference_capture_busy.emit(False)
        self._end_activity("camera")
        self._resume_queued_work()

    def validate_recipe_sample(self, recipe: Recipe) -> bool:
        """Acquire and grade one fresh known-good validation sample.

        Validation uses the same camera, locator, terminal extraction, marking
        classifier, ring detector, and evidence writer as a production cycle.
        Only PLC publication and yield counters are bypassed.
        """

        if (
            self._recipe_validation_in_flight
            or self._reference_capture_in_flight
            or self._camera_operation_in_flight
            or self._startup_in_flight
            or self._inspection_in_flight
            or self._ml_training_capture_in_flight
            or self._ml_training_in_flight
        ):
            return False
        snapshot = Recipe.from_dict(recipe.to_dict())
        self._recipe_validation_in_flight = True
        self.recipe_validation_busy.emit(True)
        self._begin_activity("validation", "VALIDATING RECIPE SAMPLE")
        task = ServiceTask(self._execute_recipe_validation, snapshot)
        task.signals.completed.connect(self._recipe_validation_complete)
        task.signals.failed.connect(self._recipe_validation_failed)
        task.signals.finished.connect(self._recipe_validation_finished)
        self.thread_pool.start(task)
        return True

    def _execute_recipe_validation(self, recipe: Recipe) -> InspectionResult:
        if not self.camera.connected:
            raise CameraError("Camera is not connected; validation sample was not captured")
        if isinstance(self.camera, MockCameraService):
            raise CameraError(
                "Recipe validation requires a physical camera. Demo Image and automatic "
                "camera fallback may be used for HMI commissioning, but their samples "
                "cannot count toward production recipe activation."
            )
        frame = self.camera.capture()
        if not frame.fresh:
            raise CameraError("Camera did not return a fresh validation frame")
        cycle_id = f"VALIDATE-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
        return self.pipeline.inspect(
            frame,
            recipe,
            trigger_source="RECIPE_VALIDATION",
            cycle_id=cycle_id,
            validation_mode=True,
        )

    def _recipe_validation_complete(self, payload: object) -> None:
        if not isinstance(payload, InspectionResult):
            self._recipe_validation_failed(
                "Validation worker returned an invalid inspection result"
            )
            return
        self.repository.save_inspection(payload.to_dict())
        self.recipe_validation_completed.emit(payload)
        self._add_event(
            "RECIPE_VALIDATION",
            f"{payload.disposition.display}: {payload.reason}",
            details={
                "cycle_id": payload.cycle_id,
                "recipe_id": payload.recipe_id,
                "recipe_name": payload.recipe_name,
                "frame_id": payload.frame_id,
                "evidence_directory": payload.evidence_directory,
                "locator_metrics": payload.locator_metrics,
            },
        )

    def _recipe_validation_failed(self, message: str) -> None:
        self.recipe_validation_failed.emit(message)
        self._add_event("RECIPE_VALIDATION", f"Validation capture failed: {message}")

    def _recipe_validation_finished(self) -> None:
        self._recipe_validation_in_flight = False
        self.recipe_validation_busy.emit(False)
        self._end_activity("validation")
        self._resume_queued_work()

    # --- which recipe grades this part --------------------------------------
    #
    # The controller no longer holds one recipe that everything grades against.
    # The PLC names a product on every trigger, and the station resolves that
    # name to the newest revision of that recipe whose validation is complete.
    # Nobody selects a recipe for production, which is what lets this station
    # run a mixed line and, eventually, run headless.
    #
    # `active_recipe` remains, and remains the station's own choice, but its
    # job is now narrower: it is what a manual trigger at the HMI grades
    # against, and what the station falls back to when no PLC selector is
    # configured. It is never consulted for a PLC-triggered cycle.

    def _requested_selector(self) -> tuple[int | None, str]:
        """The recipe the PLC is currently naming, as (number, name)."""

        state = dict(self._last_plc_state)
        selector = str(state.get("recipe_selector", self.config.plc_recipe_selector))
        if selector == "number":
            raw = state.get("recipe_number")
            return (int(raw) if raw is not None else 0), ""
        return None, str(state.get("recipe_name", "") or "").strip()

    def _plc_names_the_product(self) -> bool:
        """Is the PLC selector what decides the recipe on this station?

        A station setting, never inferred from the value read. Inferring it was
        a hazard: a selector tag that read blank -- a comm fault, a renamed
        tag, a program that does not write it yet -- looked identical to "no
        selector configured", and the station fell back to grading the part
        against whatever recipe was selected at the HMI. That is the silent
        substitution the whole refusal path exists to prevent.
        """

        return self.config.plc_recipe_source == "plc"

    def _plc_selector_is_configured(self) -> bool:
        number, name = self._requested_selector()
        return bool((number or 0) > 0 or name)

    def resolve_recipe_for_trigger(self, trigger_source: str) -> Recipe | None:
        """The recipe this trigger must be graded against, or None to refuse.

        None never means "use something else". A trigger the station cannot
        resolve is refused, because grading a product against another product's
        recipe is the one outcome worse than not inspecting it.
        """

        if trigger_source.upper() != "PLC":
            # Manual triggers, and every simulation without a PLC, grade
            # against the recipe a technician selected at the HMI.
            return self.active_recipe

        if not self._plc_names_the_product():
            # Station selection is this station's configured recipe source, so
            # a PLC trigger grades against it exactly as a manual one does.
            return self.active_recipe

        number, name = self._requested_selector()
        if not self._plc_selector_is_configured():
            # The selector names nothing. Refuse -- there is no fallback in
            # this mode, deliberately.
            return None

        return self.repository.resolve_production_recipe(
            recipe_number=number,
            recipe_name=name,
        )

    def station_ready_for_trigger(self) -> bool:
        """Can this station accept a trigger and grade the part it gets?

        This answers capability, not the momentary state of a cycle. It stays
        true while an inspection runs, because Busy already reports that and a
        readiness bit that dropped every cycle would flap at cycle rate and
        tell the controller nothing it did not have. The controller's permissive
        is Ready AND NOT Busy.

        It is false when the station could not grade a part if triggered:

        * no camera, or the camera faulted;
        * no active recipe, no reference, or an unusable model -- whatever
          inspection readiness reports as a blocking issue;
        * the camera is held by something that is not an inspection: a live
          preview, a reference capture, a validation run, an ML capture, or a
          settings apply.

        That last group matters most. Those are exactly the states where a
        trigger is silently dropped, so without this the controller learns the
        station was unavailable only by timing out.
        """

        if not self.camera.connected:
            return False
        if self._recipe_session_active:
            # A recipe is open. The station is not available for the whole of
            # that session, however idle it looks between captures.
            return False
        # Readiness follows the product the controller is currently naming. If
        # it is naming one this station cannot run -- unknown number, or no
        # validated revision -- readiness goes false, so the controller sees a
        # misconfigured line as a state rather than only as a timeout.
        target = self.resolve_recipe_for_trigger("PLC")
        if target is None:
            # Either nothing is selectable at all, or the controller is naming
            # a product this station cannot run. Both are "do not trigger me".
            # This must not fall through to inspection_readiness(), which
            # defaults a None recipe back to the station selection and would
            # report a refused product as ready.
            return False
        if not bool(self.inspection_readiness(target).get("ready")):
            return False
        return not (
            self._startup_in_flight
            or self._camera_operation_in_flight
            or self._reference_capture_in_flight
            or self._recipe_validation_in_flight
            or self._ml_training_capture_in_flight
            or self._ml_training_in_flight
            or self._camera_preview_active
        )

    def _publish_plc_ready(self, *, force: bool = False) -> None:
        """Write the readiness tag, but only when the answer changed.

        Readiness is evaluated on every health recalculation, which is often.
        Writing every time would put a needless write on the wire several times
        a second; the controller only cares about transitions.
        """

        if not str(self.config.tags.ready or "").strip():
            return
        if not self.plc.connected:
            return
        ready = self.station_ready_for_trigger()
        if not force and ready == self._plc_ready_published:
            return
        try:
            self.plc.write_ready(ready)
        except Exception as exc:  # noqa: BLE001
            self._add_event("PLC", f"Could not publish station readiness: {exc}")
            return
        if ready != self._plc_ready_published:
            self._add_event(
                "PLC",
                f"Station readiness published as {'READY' if ready else 'NOT READY'}",
            )
        self._plc_ready_published = ready

    def record_maintenance_access(self, screen: str, *, granted: bool) -> None:
        """Log an attempt to open a gated screen, refused or allowed.

        The passcode itself stops very little. What makes the gate worth having
        is this record: "who opened Settings before that recipe changed" needs
        an answer, and so does "who was trying to".
        """

        self._add_event(
            "ACCESS",
            f"{screen} {'opened' if granted else 'refused'}",
            details={"screen": screen, "granted": bool(granted)},
        )

    # --- live camera preview ------------------------------------------------
    #
    # Tuning exposure, gain, or white balance by applying a profile, taking one
    # test frame, and reading the result is guesswork with a slow feedback loop.
    # The preview streams frames while the technician moves a control, so the
    # effect is visible as it is made.
    #
    # What makes it safe to drive a production camera this way:
    #
    #   * the station counts as camera-occupied while it runs, so no inspection
    #     can be graded on settings that are still being tuned;
    #   * the settings in force are transient -- they are written to the camera
    #     and never to the station configuration;
    #   * whatever was saved is restored when the preview stops, so leaving
    #     without saving leaves the camera as it was found.

    CAMERA_PREVIEW_INTERVAL_MS = 320

    @property
    def camera_preview_active(self) -> bool:
        return self._camera_preview_active

    def start_camera_preview(self) -> bool:
        """Begin streaming preview frames. False if the camera is busy."""

        if self._camera_preview_active:
            return True
        if (
            self._startup_in_flight
            or self._inspection_in_flight
            or self._camera_operation_in_flight
            or self._reference_capture_in_flight
            or self._recipe_validation_in_flight
            or self._ml_training_capture_in_flight
            or self._ml_training_in_flight
        ):
            return False

        self._camera_preview_saved = self.config.camera.normalized()
        self._camera_preview_dirty = False
        self._camera_preview_active = True
        self._publish_plc_ready()
        self.camera_preview_timer.start()
        self.camera_preview_state.emit(True, "LIVE PREVIEW RUNNING")
        self._add_event("CAMERA", "Live camera preview started")
        return True

    def stop_camera_preview(self, *, restore: bool = True) -> None:
        """Stop streaming and, by default, put the saved settings back."""

        if not self._camera_preview_active:
            return
        self.camera_preview_timer.stop()
        self._camera_preview_active = False
        self._publish_plc_ready()
        saved = self._camera_preview_saved
        self._camera_preview_saved = None

        if restore and self._camera_preview_dirty and saved is not None:
            self._camera_preview_dirty = False
            try:
                if self.camera.connected:
                    self.camera.apply_configuration(saved)
                self.camera_preview_state.emit(False, "SAVED CAMERA SETTINGS RESTORED")
                self._add_event(
                    "CAMERA",
                    "Live camera preview stopped; saved settings restored",
                )
                return
            except Exception as exc:  # noqa: BLE001
                # Say so loudly. The camera is now carrying settings that are
                # not the station's, and the technician is the only one who can
                # resolve that.
                self._add_event(
                    "CAMERA",
                    f"Saved camera settings could not be restored after preview: {exc}",
                )
                self.camera_preview_state.emit(
                    False,
                    "SAVED SETTINGS COULD NOT BE RESTORED — APPLY CAMERA SETTINGS BEFORE INSPECTING",
                )
                return

        self._camera_preview_dirty = False
        self.camera_preview_state.emit(False, "LIVE PREVIEW STOPPED")

    def preview_camera_settings(self, settings: CameraConfig) -> bool:
        """Write settings to the camera for preview only. Never persisted."""

        if not self._camera_preview_active or self._camera_preview_in_flight:
            return False
        self._camera_preview_dirty = True
        self._camera_preview_in_flight = True
        task = ServiceTask(self._preview_apply, settings.normalized())
        task.signals.completed.connect(self._camera_preview_frame_ready)
        task.signals.failed.connect(self._camera_preview_failed)
        task.signals.finished.connect(self._camera_preview_finished)
        self.thread_pool.start(task)
        return True

    def _preview_apply(self, settings: CameraConfig) -> object:
        if not self.camera.connected:
            self.camera.connect()
        self.camera.apply_configuration(settings)
        return self.camera.grab()

    def _camera_preview_tick(self) -> None:
        if not self._camera_preview_active or self._camera_preview_in_flight:
            return
        if self._inspection_in_flight or self._camera_operation_in_flight:
            return
        self._camera_preview_in_flight = True
        task = ServiceTask(self._preview_grab)
        task.signals.completed.connect(self._camera_preview_frame_ready)
        task.signals.failed.connect(self._camera_preview_failed)
        task.signals.finished.connect(self._camera_preview_finished)
        self.thread_pool.start(task)

    def _preview_grab(self) -> object:
        if not self.camera.connected:
            self.camera.connect()
        return self.camera.grab()

    def _camera_preview_frame_ready(self, payload: object) -> None:
        if self._camera_preview_active:
            self.camera_preview_frame.emit(payload)

    def _camera_preview_failed(self, message: str) -> None:
        self.camera_preview_timer.stop()
        self._camera_preview_active = False
        self._camera_preview_saved = None
        self._camera_preview_dirty = False
        self.camera_preview_state.emit(False, f"LIVE PREVIEW STOPPED — {message}")
        self._add_event("CAMERA", f"Live camera preview stopped: {message}")

    def _camera_preview_finished(self) -> None:
        self._camera_preview_in_flight = False

    def apply_camera_settings(
        self,
        settings: CameraConfig,
        updated_configuration: AppConfig | None = None,
    ) -> bool:
        """Apply settings, verify a frame when possible, then persist them.

        ``False`` means another camera operation is already pending/in progress.
        Startup and inspection ownership do not reject the request; the profile is
        queued and begins when the camera becomes available. Hardware failures are
        delivered asynchronously through ``camera_operation_failed``.
        """

        if self._camera_operation_in_flight:
            return False
        self._pending_camera_settings = settings.normalized()
        self._pending_configuration = updated_configuration
        self._set_camera_operation_busy(True)
        if (
            self._startup_in_flight
            or self._inspection_in_flight
            or self._reference_capture_in_flight
            or self._recipe_validation_in_flight
            or self._ml_training_capture_in_flight
        ):
            if self._inspection_in_flight:
                reason = "current inspection"
            elif self._recipe_validation_in_flight:
                reason = "recipe validation capture"
            elif self._reference_capture_in_flight:
                reason = "recipe reference capture"
            elif self._ml_training_capture_in_flight:
                reason = "ML training image capture"
            else:
                reason = "startup camera initialization"
            self.camera_operation_queued.emit(
                f"Camera settings queued until {reason} finishes."
            )
            return True
        self._start_camera_settings_task()
        return True

    def _start_camera_settings_task(self) -> None:
        if (
            not self._camera_operation_in_flight
            or self._camera_apply_task_started
            or self._pending_camera_settings is None
            or self._startup_in_flight
            or self._inspection_in_flight
            or self._reference_capture_in_flight
            or self._recipe_validation_in_flight
            or self._ml_training_capture_in_flight
        ):
            return
        self._camera_apply_task_started = True
        self._begin_activity("camera", "CONFIGURING CAMERA")
        task = ServiceTask(self._apply_camera_settings, self._pending_camera_settings)
        task.signals.completed.connect(self._camera_settings_complete)
        task.signals.failed.connect(self._camera_operation_failed)
        task.signals.finished.connect(self._camera_settings_finished)
        self.thread_pool.start(task)

    def _apply_camera_settings(self, settings: CameraConfig) -> dict[str, Any]:
        previous_settings = self.config.camera.normalized()
        try:
            requested_backend = (
                self._pending_configuration.camera_backend
                if self._pending_configuration is not None
                else self.config.camera_backend
            )
            self._ensure_camera_service_for_backend(requested_backend, settings)
            if not self.camera.connected:
                self.camera.connect()
            state = self.camera.apply_configuration(settings)
            capabilities = state.capabilities
            effective_settings = state.settings.normalized()
            if effective_settings.trigger_mode == "On" and effective_settings.trigger_source != "Software":
                width, height = capabilities.active_resolution
                return {
                    "capabilities": capabilities,
                    "settings": effective_settings,
                    "frame_width": width,
                    "frame_height": height,
                    "channels": 0,
                    "mean_level": 0.0,
                    "test_skipped": True,
                    "test_message": "External-trigger profile applied; test frame waits for the configured input.",
                    "preview_frame": None,
                    "camera_backend": self.camera_backend_active,
                    "camera_description": self.camera.description,
                }
            frame = self.camera.grab()
            preview_scale = min(1.0, 1280.0 / frame.shape[1], 720.0 / frame.shape[0])
            if preview_scale < 1.0:
                preview_frame = cv2.resize(
                    frame,
                    (int(round(frame.shape[1] * preview_scale)), int(round(frame.shape[0] * preview_scale))),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                preview_frame = frame.copy()
            return {
                "capabilities": capabilities,
                "settings": effective_settings,
                "frame_width": int(frame.shape[1]),
                "frame_height": int(frame.shape[0]),
                "channels": int(frame.shape[2]) if frame.ndim == 3 else 1,
                "mean_level": float(frame.mean()),
                "test_skipped": False,
                "test_message": "",
                "preview_frame": preview_frame,
                "camera_backend": self.camera_backend_active,
                "camera_description": self.camera.description,
            }
        except Exception as exc:
            # A profile is not considered accepted until the verification grab succeeds.
            # Restore the last persisted profile if the post-write test fails.
            try:
                if self.camera.connected:
                    self.camera.apply_configuration(previous_settings)
            except Exception as rollback_exc:
                raise RuntimeError(
                    f"{exc}; the previous camera profile could not be restored: {rollback_exc}"
                ) from exc
            raise

    def _ensure_camera_service_for_backend(self, backend: str, settings: CameraConfig) -> None:
        """Select a camera service for Apply & Test without using a serial/model lock."""

        backend = str(backend or "auto").strip().lower()
        if backend not in {"auto", "basler", "simulation"}:
            backend = "auto"

        if backend == "simulation":
            if isinstance(self.camera, MockCameraService) and self.camera_backend_active == "simulation":
                return
            replacement = MockCameraService(
                self.assets_directory / "demo_battery.jpg",
                settings,
            )
            replacement.connect()
            self._replace_camera_service(replacement, "simulation")
            return

        if isinstance(self.camera, BaslerCameraService):
            self.camera_backend_active = "basler"
            return

        # Auto and Basler-required modes both open device zero from pypylon.
        # Apply & Test must never silently validate camera settings against a demo
        # image when physical hardware was requested.
        replacement = BaslerCameraService(settings)
        try:
            replacement.connect()
        except Exception:
            replacement.disconnect()
            raise
        self._replace_camera_service(replacement, "basler")
        self.camera_fallback_reason = ""

    def _replace_camera_service(self, replacement, active_backend: str) -> None:
        previous = self.camera
        self.camera = replacement
        self.camera_backend_active = active_backend
        try:
            previous.disconnect()
        except Exception:  # noqa: S110 - best-effort teardown of the service just replaced
            pass

    def _camera_settings_complete(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        effective_settings = result.get("settings")
        if self._pending_configuration is not None:
            requested = self._pending_configuration.normalized()
            camera_settings = (
                effective_settings.normalized()
                if isinstance(effective_settings, CameraConfig)
                else requested.camera
            )
            # Merge only camera/general fields into the latest controller config.
            # Camera and PLC configuration workers are intentionally independent;
            # assigning an older whole-config snapshot here could undo a PLC change
            # that completed while the camera test was running.
            self.config = replace(
                self.config,
                camera_backend=requested.camera_backend,
                camera=camera_settings,
                fullscreen=requested.fullscreen,
                operator_name=requested.operator_name,
            ).normalized()
            self.plc_poll_timer.setInterval(self.config.plc_poll_ms)
            self.config.save(self.config_path)
            self.configuration_changed.emit(self.config)
        elif isinstance(effective_settings, CameraConfig):
            self.config = replace(self.config, camera=effective_settings.normalized()).normalized()
            self.config.save(self.config_path)
            self.configuration_changed.emit(self.config)
        self._pending_configuration = None
        capabilities = result.get("capabilities")
        if capabilities is not None:
            self.camera_capabilities_changed.emit(capabilities)
        self.camera_test_completed.emit(result)
        camera_text = "OK"
        if self.camera_backend_active == "simulation":
            camera_text = "SIMULATION"
        elif self.camera_backend_active == "simulation_fallback":
            camera_text = "SIM FALLBACK"
        elif self.camera_backend_active == "basler_defaults":
            camera_text = "CAM DEFAULTS"
        camera_health_ok = self.camera_backend_active != "simulation_fallback"
        self.health["camera"] = {"ok": camera_health_ok, "text": camera_text}
        self._recalculate_system_health()
        self.health_changed.emit(self.health)
        action = "applied" if result.get("test_skipped") else "verified"
        self._add_event(
            "CAMERA",
            f"Camera settings {action} at {result['frame_width']} x {result['frame_height']}",
            details={
                "resolution": [result["frame_width"], result["frame_height"]],
                "mean_level": result["mean_level"],
                "test_skipped": bool(result.get("test_skipped")),
            },
        )

    def _camera_operation_failed(self, message: str) -> None:
        self._pending_configuration = None
        self.camera_operation_failed.emit(message)
        self.health["camera"] = {"ok": False, "text": "CONFIG FAULT"}
        self._recalculate_system_health()
        self.health_changed.emit(self.health)
        self._add_event("CAMERA", message)

    def _camera_settings_finished(self) -> None:
        self._camera_apply_task_started = False
        self._pending_camera_settings = None
        self._set_camera_operation_busy(False)
        self._end_activity("camera")
        self._resume_queued_work()

    def _set_camera_operation_busy(self, busy: bool) -> None:
        self._camera_operation_in_flight = busy
        self.camera_operation_busy.emit(busy)

    def _recalculate_system_health(self) -> None:
        self.health["disk"] = disk_health(self.data_directory)
        camera_ok = bool(self.health.get("camera", {}).get("ok"))
        plc_ok = bool(self.health.get("plc", {}).get("ok"))
        readiness = self.pipeline.readiness_issues(self.active_recipe)
        vision_ok = not readiness
        self.health["vision"] = {
            "ok": vision_ok,
            "text": "READY" if vision_ok else "NOT READY",
            "issues": readiness,
        }
        # Disk and lighting stay out of this calculation deliberately. Station
        # run state is a change-controlled contract (README change-control
        # invariants), so a low-disk warning reports to the technician without
        # silently taking the station out of production.
        ok = camera_ok and plc_ok and vision_ok
        if not camera_ok or not plc_ok:
            text = "DEGRADED"
        elif not vision_ok:
            text = "NOT READY"
        else:
            text = "GOOD"
        self.health["system"] = {"ok": ok, "text": text}
        # Health is recalculated on every state change that could affect
        # whether a trigger would succeed, which makes it the right place to
        # re-evaluate readiness. The write itself only happens on a transition.
        self._publish_plc_ready()

    def apply_plc_settings(self, updated_configuration: AppConfig) -> bool:
        """Connect and verify a replacement PLC service, then make it active.

        Simulation is a real selectable backend, not a restart-only display option.
        The existing service stays active if a requested pycomm3 connection fails.
        """

        if self._plc_operation_in_flight:
            return False
        # A technician applying settings supersedes any pending automatic
        # reconnection, and their attempt starts the backoff from scratch.
        self.cancel_plc_reconnect()
        self.plc_poll_timer.stop()
        self.plc_heartbeat_timer.stop()
        self._pending_plc_configuration = updated_configuration.normalized()
        self._plc_apply_task_started = False
        self._set_plc_operation_busy(True)

        # A poll may have started just before the technician pressed Apply. Keep
        # the request accepted and launch it as soon as that read completes,
        # rather than making the technician repeatedly press the button.
        if (
            not self._plc_poll_in_flight
            and not self._startup_in_flight
            and not self._inspection_in_flight
        ):
            self._start_plc_settings_task()
        return True

    def _start_plc_settings_task(self) -> None:
        if (
            not self._plc_operation_in_flight
            or self._plc_apply_task_started
            or self._pending_plc_configuration is None
            or self._plc_poll_in_flight
            or self._startup_in_flight
            or self._inspection_in_flight
        ):
            return
        self._plc_apply_task_started = True
        self._begin_activity("plc", "CONFIGURING PLC")
        task = ServiceTask(self._configure_plc_service, self._pending_plc_configuration)
        task.signals.completed.connect(self._plc_settings_complete)
        task.signals.failed.connect(self._handle_plc_operation_failed)
        task.signals.finished.connect(self._plc_settings_finished)
        self.thread_pool.start(task)

    def _configure_plc_service(self, updated: AppConfig) -> dict[str, Any]:
        replacement = self._build_plc_service(updated)
        active_backend = updated.plc_backend
        try:
            replacement.connect()
            replacement.clear_result()
            cycle_state = replacement.read_cycle_state()
            cycle_state["heartbeat"] = replacement.write_heartbeat(False)
        except Exception as exc:
            try:
                replacement.disconnect()
            except Exception:  # noqa: S110 - best-effort teardown; the original failure is re-raised below
                pass
            raise exc

        self._replace_plc_service(replacement, active_backend)

        return {
            "backend": updated.plc_backend,
            "active_backend": active_backend,
            "fallback": False,
            "fallback_error": "",
            "description": replacement.description,
            "driver": self.plc_driver_name,
            "connected": replacement.connected,
            "cycle_state": cycle_state,
            "address": updated.plc_address,
            "poll_ms": updated.plc_poll_ms,
            "heartbeat_ms": updated.plc_heartbeat_ms,
        }

    def _plc_settings_complete(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        requested = (self._pending_plc_configuration or self.config).normalized()
        # Merge only PLC/general fields into the newest controller config so an
        # independently completed camera Apply & Test cannot be overwritten by
        # an older PLC settings snapshot.
        self.config = replace(
            self.config,
            plc_backend=requested.plc_backend,
            plc_address=requested.plc_address,
            plc_poll_ms=requested.plc_poll_ms,
            plc_heartbeat_ms=requested.plc_heartbeat_ms,
            plc_recipe_selector=requested.plc_recipe_selector,
            tags=requested.tags,
            fullscreen=requested.fullscreen,
            operator_name=requested.operator_name,
        ).normalized()
        self.config.save(self.config_path)
        self.plc_poll_timer.setInterval(self.config.plc_poll_ms)
        self.plc_heartbeat_timer.setInterval(self.config.plc_heartbeat_ms)
        if self.plc.connected and not self.plc_poll_timer.isActive():
            self.plc_poll_timer.start()
        if self.plc.connected and not self.plc_heartbeat_timer.isActive():
            self.plc_heartbeat_timer.start()

        simulated = isinstance(self.plc, MockPlcService)
        self.health["plc"] = {
            "ok": bool(self.plc.connected),
            "text": (
                "SIMULATION" if simulated else ("OK" if self.plc.connected else "FAULT")
            ),
        }
        self._recalculate_system_health()
        self.health_changed.emit(self.health)
        cycle_state = dict(result.get("cycle_state", {}) or {})
        self._last_plc_state.update(cycle_state)
        if "bypass" in cycle_state:
            self._bypass_active = bool(cycle_state.get("bypass"))
            self._bypass_known = True
        self.configuration_changed.emit(self.config)
        self.plc_test_completed.emit(result)
        self._pending_plc_configuration = None
        self._last_plc_recipe_mismatch = ""
        self._add_event(
            "PLC",
            (
                "PLC simulation enabled"
                if simulated
                else f"PLC connection verified at {self.config.plc_address}"
            ),
            details={
                "backend": self.config.plc_backend,
                "active_backend": self.plc_backend_active,
                "recipe_selector": self.config.plc_recipe_selector,
                "driver": self.plc_driver_name,
                "poll_ms": self.config.plc_poll_ms,
                "heartbeat_ms": self.config.plc_heartbeat_ms,
            },
        )
        self._emit_plc_simulation_state()

    def _handle_plc_operation_failed(self, message: str) -> None:
        self._pending_plc_configuration = None
        active_ok = bool(self.plc.connected)
        if isinstance(self.plc, MockPlcService) and active_ok:
            active_text = "SIMULATION"
        elif active_ok:
            active_text = "OK"
        else:
            active_text = "FAULT"
        self.health["plc"] = {"ok": active_ok, "text": active_text}
        self._recalculate_system_health()
        self.health_changed.emit(self.health)
        self.plc_operation_failed.emit(message)
        self._add_event("PLC", f"PLC configuration failed: {message}")
        self._emit_plc_simulation_state()

    def _plc_settings_finished(self) -> None:
        self._plc_apply_task_started = False
        if self.plc.connected and not self.plc_poll_timer.isActive():
            self.plc_poll_timer.start()
        if self.plc.connected and not self.plc_heartbeat_timer.isActive():
            self.plc_heartbeat_timer.start()
        self._set_plc_operation_busy(False)
        self._end_activity("plc")
        # A replacement service, or a changed tag map, has been told nothing.
        self._publish_plc_ready(force=True)
        self._assert_recipe_session_busy()
        self._resume_queued_work()

    def _set_plc_operation_busy(self, busy: bool) -> None:
        self._plc_operation_in_flight = busy
        self.plc_operation_busy.emit(busy)

    def pulse_simulated_plc_trigger(self) -> bool:
        if not isinstance(self.plc, MockPlcService) or not self.plc.connected:
            return False
        self.plc.pulse_trigger()
        if not self.plc_poll_timer.isActive():
            self.plc_poll_timer.start()
        self._add_event("PLC", "Simulated PLC trigger requested")
        self._emit_plc_simulation_state()
        return True

    def run_inspection(self, trigger_source: str = "MANUAL") -> bool:
        source = trigger_source.upper()
        camera_occupied = (
            self._startup_in_flight
            or self._inspection_in_flight
            or self._camera_operation_in_flight
            or self._reference_capture_in_flight
            or self._recipe_validation_in_flight
            or self._ml_training_capture_in_flight
            or self._ml_training_in_flight
            # A live preview may be driving the camera with settings that have
            # not been saved and have not been validated against any recipe.
            # Nothing may be graded on them.
            or self._camera_preview_active
        )
        if camera_occupied:
            # A PLC edge may already have been consumed by the polling service.
            # Preserve one pending PLC cycle; manual requests remain explicit.
            if source == "PLC":
                self._pending_inspection_trigger_source = source
            return False

        if source == "PLC" and self._recipe_session_active:
            # Busy has been high for the whole session, so a trigger arriving
            # now is the controller ignoring its own interlock. Refuse rather
            # than grading a part a technician is holding.
            if not self._recipe_session_trigger_reported:
                self._add_event(
                    "PLC",
                    "PLC triggered while a recipe was open for editing. The trigger "
                    "was refused; Busy is held high for the whole session.",
                )
                self._recipe_session_trigger_reported = True
            return False

        # The recipe this trigger resolved to, not the station's selection: for
        # a PLC cycle those are unrelated.
        target = self.resolve_recipe_for_trigger(source)
        if source == "PLC" and target is None:
            # The controller named a product this station cannot run. Refuse
            # rather than grading it against anything else; the refusal has
            # already been logged where the selector was read.
            return False

        self._inspection_in_flight = True
        cycle = self._begin_cycle_status(source)
        self._begin_activity("inspection", "ACQUIRING")
        recipe_snapshot = (
            Recipe.from_dict(target.to_dict()) if target is not None else None
        )
        task = ServiceTask(
            self._execute_inspection_cycle,
            recipe_snapshot,
            source,
            cycle,
        )
        task.signals.completed.connect(self._inspection_completed)
        task.signals.failed.connect(self._inspection_task_failed)
        task.signals.finished.connect(self._inspection_finished)
        self.thread_pool.start(task)
        return True

    def _execute_inspection_cycle(
        self,
        recipe: Recipe | None,
        trigger_source: str,
        cycle: InspectionCycleStatus,
    ) -> InspectionResult:
        started = perf_counter()
        plc_triggered = trigger_source == "PLC"
        if plc_triggered:
            self.plc.publish_result(passed=False, busy=True)
            # Busy clears the previous result, so nothing is outstanding until
            # this cycle publishes.
            #
            # The acknowledge latch is deliberately left alone. Resetting it
            # here would rearm it on a bit that is still high, so a controller
            # holding acknowledge would clear each new result the moment it was
            # published and would itself see nothing. Leaving the latch means
            # the bit has to be observed low before it can acknowledge again,
            # which is what "the controller took this result" actually requires.
            self._plc_result_outstanding = False
            self._emit_plc_simulation_state()

        frame: CameraFrame | None = None
        try:
            self._set_cycle_state(
                InspectionCycleState.ACQUIRING,
                "Waiting for a fresh camera frame",
            )
            frame = self.camera.capture()
            if not frame.fresh:
                raise CameraError(
                    "Camera returned a frame that does not belong to this acquisition request"
                )
            self._set_cycle_state(
                InspectionCycleState.ACQUIRING,
                f"Fresh frame {frame.frame_id} acquired",
                frame=frame,
            )

            def progress(state: InspectionCycleState, message: str) -> None:
                self._set_cycle_state(state, message, frame=frame)
                self._begin_activity("inspection", state.display)

            result = self.pipeline.inspect(
                frame,
                recipe,
                trigger_source=trigger_source,
                cycle_id=cycle.cycle_id,
                stage_callback=progress,
            )
        except CameraError as exc:
            result = self.pipeline.fault_result(
                recipe=recipe,
                trigger_source=trigger_source,
                cycle_id=cycle.cycle_id,
                reason="NO NEW CAMERA FRAME",
                details=str(exc),
                duration_ms=max(1, int((perf_counter() - started) * 1000)),
                frame=frame,
            )
        except Exception as exc:  # noqa: BLE001 - converted to explicit evidence/result
            result = self.pipeline.fault_result(
                recipe=recipe,
                trigger_source=trigger_source,
                cycle_id=cycle.cycle_id,
                reason="INTERNAL INSPECTION ERROR",
                details=str(exc),
                duration_ms=max(1, int((perf_counter() - started) * 1000)),
                frame=frame,
            )

        if plc_triggered:
            try:
                self.plc.publish_result(
                    passed=result.passed,
                    busy=False,
                )
                if self._acknowledge_configured():
                    self._plc_result_outstanding = True
                    self._plc_unacknowledged_reported = False
                self._emit_plc_simulation_state()
            except Exception as exc:  # noqa: BLE001
                self._add_event("PLC", f"Could not publish inspection result: {exc}")
        return result

    def _inspection_completed(self, payload: object) -> None:
        if not isinstance(payload, InspectionResult):
            self._inspection_task_failed("Inspection worker returned an invalid result")
            return
        result = payload
        self._accept_inspection(result, increment_counts=True)
        if result.disposition in {InspectionDisposition.PASS, InspectionDisposition.REJECT}:
            state = InspectionCycleState.COMPLETE
        elif result.disposition == InspectionDisposition.NOT_READY:
            state = InspectionCycleState.NOT_READY
        else:
            state = InspectionCycleState.FAULT
        self._set_cycle_state(state, result.reason)

    def _inspection_task_failed(self, message: str) -> None:
        cycle = self.cycle_status
        try:
            result = self.pipeline.fault_result(
                recipe=self.active_recipe,
                trigger_source=cycle.trigger_source or "UNKNOWN",
                cycle_id=cycle.cycle_id or f"CYCLE-{uuid4().hex[:8]}",
                reason="INSPECTION WORKER FAILURE",
                details=message,
            )
        except Exception as evidence_exc:  # noqa: BLE001 - last-resort HMI fault
            result = InspectionResult.create(
                recipe=self.active_recipe,
                disposition=InspectionDisposition.SYSTEM_FAULT,
                reason="INSPECTION WORKER / EVIDENCE FAILURE",
                duration_ms=1,
                trigger_source=cycle.trigger_source or "UNKNOWN",
                image_quality="UNKNOWN",
                full_image_path="",
                terminals=[],
                cycle_id=cycle.cycle_id or f"CYCLE-{uuid4().hex[:8]}",
                analysis_ready=False,
                readiness_issues=[
                    "SYSTEM_FAULT",
                    f"WORKER:{message}",
                    f"EVIDENCE:{evidence_exc}",
                ],
                locator_status=self.pipeline.battery_locator.status,
                classifier_status=self.pipeline.classifier_status_for_recipe(
                    self.active_recipe
                ),
            )
        self._accept_inspection(result, increment_counts=False)
        self._set_cycle_state(InspectionCycleState.FAULT, result.reason)

    def _inspection_finished(self) -> None:
        self._inspection_in_flight = False
        self._end_activity("inspection")
        self._resume_queued_work()

    def _resume_queued_work(self) -> None:
        if (
            self._startup_in_flight
            or self._inspection_in_flight
            or self._reference_capture_in_flight
            or self._recipe_validation_in_flight
            or self._ml_training_capture_in_flight
            or self._ml_training_in_flight
        ):
            return
        self._start_camera_settings_task()
        self._start_plc_settings_task()
        if self._camera_operation_in_flight or self._plc_operation_in_flight:
            return
        if self._pending_inspection_trigger_source:
            source = self._pending_inspection_trigger_source
            self._pending_inspection_trigger_source = None
            self.run_inspection(source)

    def _heartbeat_tick(self) -> None:
        """Toggle the HMI heartbeat independently of inspection-cycle polling.

        The heartbeat must keep changing even while the camera/vision pipeline is
        busy so PLC ladder logic can distinguish a long inspection from a dead HMI.
        """

        if (
            self._plc_heartbeat_in_flight
            or self._plc_operation_in_flight
            or not self.plc.connected
        ):
            return
        desired = not self._heartbeat_value
        self._plc_heartbeat_in_flight = True
        task = ServiceTask(self.plc.write_heartbeat, desired)
        task.signals.completed.connect(self._heartbeat_complete)
        task.signals.failed.connect(self._heartbeat_failed)
        task.signals.finished.connect(self._heartbeat_finished)
        self.thread_pool.start(task)

    def _heartbeat_complete(self, payload: object) -> None:
        self._heartbeat_value = bool(payload)
        self._heartbeat_write_count += 1
        self._heartbeat_last_ok = datetime.now(timezone.utc).isoformat()
        self._heartbeat_fault_latched = False
        self._last_plc_state["heartbeat"] = self._heartbeat_value
        self._emit_plc_simulation_state()

    def _heartbeat_failed(self, message: str) -> None:
        if self._heartbeat_fault_latched:
            return
        self._heartbeat_fault_latched = True
        self._add_event(
            "PLC",
            f"PLC heartbeat write failed: {message}",
            details={"heartbeat_tag": self.config.tags.heartbeat},
        )
        # Treat heartbeat write failure the same as a failed cycle-state poll.
        self._plc_poll_failed(f"Heartbeat write failed: {message}")

    def _heartbeat_finished(self) -> None:
        self._plc_heartbeat_in_flight = False

    def request_bypass(self, enabled: bool) -> bool:
        """Set the PLC bypass tag asynchronously with read-back verification.

        Bypass never fabricates a PASS and never disables evidence collection.
        The HMI continues to inspect/record normally; PLC ladder logic uses the
        explicit bypass tag to decide whether the inspection interlock is enforced.
        """

        if (
            self._bypass_operation_in_flight
            or self._plc_operation_in_flight
            or self._inspection_in_flight
            or not self.plc.connected
        ):
            return False
        self._bypass_operation_in_flight = True
        self.bypass_operation_busy.emit(True)
        self._emit_plc_simulation_state()
        task = ServiceTask(self.plc.set_bypass, bool(enabled))
        task.signals.completed.connect(self._bypass_complete)
        task.signals.failed.connect(self._bypass_failed)
        task.signals.finished.connect(self._bypass_finished)
        self.thread_pool.start(task)
        return True

    def _bypass_complete(self, payload: object) -> None:
        actual = bool(payload)
        self._bypass_active = actual
        self._bypass_known = True
        self._last_plc_state["bypass"] = actual
        self._add_event(
            "BYPASS",
            f"Inspection bypass {'ENABLED' if actual else 'DISABLED'} from HMI",
            details={
                "bypass": actual,
                "tag": self.config.tags.bypass,
                "operator": self.config.operator_name,
                "semantics": "inspection continues; PLC interlock bypassed",
            },
        )
        self._emit_plc_simulation_state()

    def _bypass_failed(self, message: str) -> None:
        self._add_event(
            "BYPASS",
            f"Bypass change failed: {message}",
            details={"tag": self.config.tags.bypass},
        )
        self.bypass_operation_failed.emit(message)

    def _bypass_finished(self) -> None:
        self._bypass_operation_in_flight = False
        self.bypass_operation_busy.emit(False)
        self._emit_plc_simulation_state()

    def _poll_plc(self) -> None:
        if self._plc_poll_in_flight or self._busy or not self.plc.connected:
            return
        self._plc_poll_in_flight = True
        task = ServiceTask(self.plc.read_cycle_state)
        task.signals.completed.connect(self._handle_plc_state)
        task.signals.failed.connect(self._plc_poll_failed)
        task.signals.finished.connect(self._plc_poll_finished)
        self.thread_pool.start(task)

    def _plc_poll_finished(self) -> None:
        self._plc_poll_in_flight = False
        if self._plc_operation_in_flight and not self._plc_apply_task_started:
            self._start_plc_settings_task()

    def _plc_poll_failed(self, message: str) -> None:
        # Stop repeated fault logging. Polling resumes when a reconnection
        # attempt succeeds, or on an explicit Apply & Test PLC.
        self.plc_poll_timer.stop()
        self.plc_heartbeat_timer.stop()
        self._bypass_known = False
        self.health["plc"] = {"ok": False, "text": "FAULT"}
        self._recalculate_system_health()
        self.health_changed.emit(self.health)
        if not self._plc_reconnect_reported:
            self._add_event("PLC", f"PLC polling stopped: {message}")
            self._plc_reconnect_reported = True
        self._schedule_plc_reconnect()

    # --- getting the PLC back -------------------------------------------------
    #
    # A lost connection used to be terminal: both timers stopped, the station
    # went to FAULT, and nothing tried again until a technician walked to the
    # HMI and pressed APPLY & TEST. A switch reboot, a controller download, or a
    # cable knocked at shift change took the station out for as long as it took
    # somebody to notice.
    #
    # "Never falls back to Simulation" and "never retries" are not the same
    # rule. Reconnection re-establishes the *configured* backend and nothing
    # else: the mode never changes, the station stays FAULT and Ready stays
    # false until a real read succeeds, and the heartbeat stays stopped while
    # disconnected so the controller's own watchdog still sees a dead HMI.

    PLC_RECONNECT_FIRST_MS = 2_000
    PLC_RECONNECT_MAX_MS = 30_000

    def _schedule_plc_reconnect(self) -> None:
        if self._plc_backend_is_simulated():
            # A simulated PLC cannot lose a connection it never had over a wire,
            # and retrying one hides a genuine defect behind a retry loop.
            return
        if self._plc_reconnect_in_flight or self.plc_reconnect_timer.isActive():
            return
        if self._plc_reconnect_delay_ms <= 0:
            self._plc_reconnect_delay_ms = self.PLC_RECONNECT_FIRST_MS
        else:
            self._plc_reconnect_delay_ms = min(
                self._plc_reconnect_delay_ms * 2,
                self.PLC_RECONNECT_MAX_MS,
            )
        self.plc_reconnect_timer.start(self._plc_reconnect_delay_ms)

    def _plc_backend_is_simulated(self) -> bool:
        """Whether the station is running the simulated backend.

        Read from the active backend rather than the service's class: which
        mode the station is in is a configuration fact, and a test double or a
        future driver that happens to share a base class is not evidence about
        it either way.
        """

        return str(self.plc_backend_active or "").strip().lower() == "simulation"

    def cancel_plc_reconnect(self) -> None:
        """Stand down automatic reconnection, after a deliberate apply or test."""

        self.plc_reconnect_timer.stop()
        self._plc_reconnect_delay_ms = 0
        self._plc_reconnect_attempts = 0
        self._plc_reconnect_reported = False

    def _attempt_plc_reconnect(self) -> None:
        if self._plc_reconnect_in_flight or self._plc_operation_in_flight:
            # A technician is applying PLC settings by hand. Theirs wins; try
            # again afterwards only if it did not fix things.
            self._schedule_plc_reconnect()
            return
        if self.plc.connected and self.plc_poll_timer.isActive():
            self.cancel_plc_reconnect()
            return
        self._plc_reconnect_in_flight = True
        self._plc_reconnect_attempts += 1
        task = ServiceTask(self._reconnect_plc)
        task.signals.completed.connect(self._plc_reconnect_succeeded)
        task.signals.failed.connect(self._plc_reconnect_failed)
        task.signals.finished.connect(self._plc_reconnect_finished)
        self.thread_pool.start(task)

    def _reconnect_plc(self) -> dict[str, Any]:
        """Reopen the configured driver and prove it with one real read."""

        try:
            self.plc.disconnect()
        except Exception:  # noqa: BLE001, S110 - the old handle is being discarded
            # Nothing to log and nothing to do: this handle is already broken,
            # which is why a reconnection is being attempted at all.
            pass
        self.plc.connect()
        # Connecting is not evidence. A driver can open against a controller
        # that will not answer for the tags this station uses, so the read is
        # what decides whether the connection is usable.
        return dict(self.plc.read_cycle_state())

    def _plc_reconnect_succeeded(self, payload: object) -> None:
        attempts = self._plc_reconnect_attempts
        self.cancel_plc_reconnect()
        self.health["plc"] = {"ok": True, "text": "OK"}
        self._recalculate_system_health()
        self.health_changed.emit(self.health)
        if not self.plc_poll_timer.isActive():
            self.plc_poll_timer.start()
        if not self.plc_heartbeat_timer.isActive():
            self.plc_heartbeat_timer.start()
        self._heartbeat_fault_latched = False
        # The controller has been told nothing since the connection dropped, and
        # a reconnected one has no memory of what it was told before.
        self._publish_plc_ready(force=True)
        self._assert_recipe_session_busy()
        self._add_event(
            "PLC",
            f"PLC connection re-established after {attempts} attempt(s); polling resumed",
        )
        self._handle_plc_state(payload)

    def _plc_reconnect_failed(self, message: str) -> None:
        # Deliberately not logged per attempt: the first failure is already in
        # the audit trail, and a controller down overnight would otherwise write
        # thousands of identical rows. The recovery is what gets logged.
        del message
        self._schedule_plc_reconnect()

    def _plc_reconnect_finished(self) -> None:
        self._plc_reconnect_in_flight = False

    def _handle_plc_state(self, payload: object) -> None:
        state = dict(payload)  # type: ignore[arg-type]
        self._last_plc_state.update(state)
        if "bypass" in state:
            bypass = bool(state.get("bypass"))
            changed = (not self._bypass_known) or bypass != self._bypass_active
            self._bypass_active = bypass
            self._bypass_known = True
            if changed:
                self._add_event(
                    "BYPASS",
                    f"PLC bypass {'enabled' if bypass else 'disabled'} by PLC/read-back",
                    details={"source": "PLC_READBACK", "bypass": bypass},
                )
        self._emit_plc_simulation_state()
        self._handle_plc_acknowledge(state)
        trigger_level = bool(state.get("trigger", False))
        trigger_edge = self._plc_trigger_edge.observe(trigger_level)
        selector = str(state.get("recipe_selector", self.config.plc_recipe_selector))
        if selector == "number":
            raw_number = state.get("recipe_number")
            requested_number = int(raw_number) if raw_number is not None else 0
            requested_display = str(requested_number) if requested_number > 0 else ""
        else:
            requested_display = str(state.get("recipe_name", "")).strip()
        # The controller names the product; the station resolves it. There is no
        # station-selected recipe for the request to disagree with any more, so
        # the only question is whether this station can run what was asked.
        if self._plc_names_the_product() and self.resolve_recipe_for_trigger("PLC") is None:
            unavailable = f"{selector}:{requested_display}"
            if not self._plc_selector_is_configured():
                message = (
                    f"PLC selector tag {self.config.tags.recipe_name} named no "
                    "product, so no part can be graded. Check that the "
                    "controller writes the tag, or set the station recipe "
                    "source to the station selection"
                )
            else:
                message = (
                    f"PLC requested recipe {requested_display}, which this station "
                    "cannot run: no validated revision exists for it"
                )
            if unavailable != self._last_plc_recipe_mismatch:
                self._add_event(
                    "PLC",
                    message,
                    details={"selector": selector, "requested": requested_display},
                )
                self._last_plc_recipe_mismatch = unavailable
                # Readiness reflects the requested product, so this transition
                # has to reach the controller.
                self._publish_plc_ready()
            return
        if self._last_plc_recipe_mismatch:
            self._last_plc_recipe_mismatch = ""
            self._publish_plc_ready()
        if trigger_edge:
            if self._plc_result_outstanding and not self._plc_unacknowledged_reported:
                # The controller is triggering again without having taken the
                # previous result. Publishing the new cycle overwrites it, so
                # say so once rather than letting a result disappear silently.
                # This is not a reason to refuse the trigger: stalling the line
                # over a controller-side sequencing fault would be worse, and
                # the PLC owns that sequence.
                self._add_event(
                    "PLC",
                    "PLC triggered a new inspection before acknowledging the previous result",
                    details={"acknowledge_tag": self.config.tags.acknowledge},
                )
                self._plc_unacknowledged_reported = True
            self.run_inspection("PLC")

    def _acknowledge_configured(self) -> bool:
        """Blank tag means the handshake is off and results stay latched."""

        return bool(str(self.config.tags.acknowledge or "").strip())

    def _handle_plc_acknowledge(self, state: dict[str, Any]) -> None:
        """Clear a published result once the controller says it has taken it.

        Without this handshake the result stays on the tags until the next
        cycle raises Busy, so the PLC has to treat Complete as the validity of
        whatever it last read. With it, the station clears Busy, Complete,
        Pass, and Fail together as soon as the acknowledge bit rises, and the
        controller can treat Complete as a one-shot.

        The station never sets a result here. Acknowledgement can only clear.
        """

        if not self._acknowledge_configured():
            return
        level = state.get("acknowledge")
        if level is None:
            return
        if not self._plc_acknowledge_edge.observe(bool(level)):
            return
        if not self._plc_result_outstanding:
            return
        try:
            self.plc.clear_result()
        except Exception as exc:  # noqa: BLE001
            self._add_event("PLC", f"Could not clear the acknowledged result: {exc}")
            return
        self._plc_result_outstanding = False
        self._plc_unacknowledged_reported = False
        self._emit_plc_simulation_state()

    def _accept_inspection(self, result: InspectionResult, *, increment_counts: bool) -> None:
        self.last_inspection = result
        if increment_counts and result.is_product_result:
            self.part_count += 1
            if result.passed:
                self.pass_count += 1
            else:
                self.fail_count += 1
            self.recent_results.append(result.passed)
        if not result.passed:
            self.repository.save_inspection(result.to_dict())
            # A new reject joins the review queue immediately: the page can be
            # open on the station while the line runs, and a queue that only
            # updated when somebody pressed REFRESH would quietly go stale.
            self.failures_changed.emit()
        self.inspection_updated.emit(result)
        self.counts_changed.emit(self.counts_payload())
        self._recalculate_system_health()
        self.health_changed.emit(self.health)
        if not result.passed:
            self._add_event(
                "INSPECTION",
                f"{result.disposition.display}: {result.reason}",
                details={
                    "inspection_id": result.inspection_id,
                    "cycle_id": result.cycle_id,
                    "capture_id": result.capture_id,
                    "frame_id": result.frame_id,
                    "recipe": result.recipe_name,
                    "evidence_directory": result.evidence_directory,
                    "bypass_active": self._bypass_active,
                },
            )

    def counts_payload(self) -> dict[str, Any]:
        return {
            "part_count": self.part_count,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "reject_rate": self.reject_rate,
            "recent": list(self.recent_results),
        }

    def reset_production_counters(self) -> bool:
        """Clear session-only production yield without deleting inspection data."""

        if self.busy:
            return False
        self.part_count = 0
        self.pass_count = 0
        self.fail_count = 0
        self.recent_results.clear()
        self.counts_changed.emit(self.counts_payload())
        return True

    def list_recipes(self) -> list[Recipe]:
        return self.repository.list_latest_recipes()

    def next_recipe_number(self) -> int:
        return self.repository.next_recipe_number()

    def _assert_recipe_can_activate(self, recipe: Recipe) -> None:
        if not recipe.validation_complete:
            raise ValueError(
                "The recipe must complete all configuration-bound real guided "
                "validation runs before activation. Legacy numeric pass counts "
                "without matching evidence records are not accepted."
            )
        issues = self.pipeline.readiness_issues(recipe)
        if issues:
            raise ValueError(
                "The recipe cannot be activated because the inspection engine is not ready: "
                + "; ".join(issues)
            )

    def save_recipe(self, recipe: Recipe, *, activate: bool = False) -> Recipe:
        if recipe.recipe_number <= 0:
            recipe.recipe_number = self.repository.next_recipe_number()
        duplicate = next(
            (
                item
                for item in self.repository.list_latest_recipes()
                if item.name.casefold() == recipe.name.casefold()
                and item.recipe_id != recipe.recipe_id
            ),
            None,
        )
        if duplicate is not None:
            raise ValueError(
                f"A recipe named {recipe.name} already exists. "
                "Edit that recipe or choose a unique production name."
            )

        duplicate_number = next(
            (
                item
                for item in self.repository.list_latest_recipes()
                if item.recipe_number == recipe.recipe_number
                and item.recipe_id != recipe.recipe_id
            ),
            None,
        )
        if duplicate_number is not None:
            raise ValueError(
                f"Recipe number {recipe.recipe_number} is already assigned to "
                f"{duplicate_number.name}. Choose a unique recipe number."
            )

        latest = self.repository.get_recipe(recipe.recipe_id)
        if latest is not None and recipe.revision <= latest.revision:
            # A long-running edit wizard must not overwrite a revision created in
            # another session. Rebase it onto the latest immutable revision.
            recipe.revision = latest.revision + 1
            recipe.status = RecipeStatus.DRAFT
            recipe.validation_runs_passed = 0
            recipe.validation_records = []
            recipe.validation_configuration_hash = ""

        if recipe.reference_image is None or not recipe.reference_image.path:
            raise ValueError(
                "A captured and accepted reference image is required before this recipe can be saved."
            )
        recipe.reference_image = persist_recipe_reference(
            recipe.reference_image,
            self.data_directory,
            recipe_id=recipe.recipe_id,
            revision=recipe.revision,
        )
        recipe.validation_records = persist_recipe_validation_records(
            recipe.validation_records,
            self.data_directory,
            recipe_id=recipe.recipe_id,
            revision=recipe.revision,
            configuration_hash=recipe.validation_configuration_hash,
        )
        if activate:
            self._assert_recipe_can_activate(recipe)

        recipe = self.repository.save_recipe(
            recipe,
            username=self.config.operator_name,
            message=f"Saved recipe revision {recipe.revision} with reference image",
        )
        if activate:
            recipe = self.repository.activate_recipe(
                recipe.recipe_id,
                recipe.revision,
                username=self.config.operator_name,
            )
            self.active_recipe = recipe
            if isinstance(self.plc, MockPlcService):
                self.plc.recipe_name = recipe.name
                self.plc.recipe_number = recipe.recipe_number
            self.active_recipe_changed.emit(recipe)
        self.recipes_changed.emit(self.list_recipes())
        self._recalculate_system_health()
        self.health_changed.emit(self.health)
        return recipe

    def activate_recipe(self, recipe: Recipe) -> None:
        self._assert_recipe_can_activate(recipe)
        self.active_recipe = self.repository.activate_recipe(
            recipe.recipe_id,
            recipe.revision,
            username=self.config.operator_name,
        )
        if isinstance(self.plc, MockPlcService):
            self.plc.recipe_name = self.active_recipe.name
            self.plc.recipe_number = self.active_recipe.recipe_number
        self.active_recipe_changed.emit(self.active_recipe)
        self.recipes_changed.emit(self.list_recipes())
        self._recalculate_system_health()
        self.health_changed.emit(self.health)
        self._add_event(
            "RECIPE",
            f"Activated recipe {self.active_recipe.recipe_number} — "
            f"{self.active_recipe.name} revision {self.active_recipe.revision}",
        )

    def delete_recipe(self, recipe: Recipe) -> None:
        if self.active_recipe and recipe.recipe_id == self.active_recipe.recipe_id:
            raise ValueError("The active recipe cannot be deleted")
        self.repository.delete_recipe(recipe.recipe_id, username=self.config.operator_name)
        self.recipes_changed.emit(self.list_recipes())

    def audit_events(self) -> list[dict]:
        return self.repository.list_audit_events()

    def update_configuration(self, updated: AppConfig) -> None:
        self.config = updated.normalized()
        self.pipeline.set_failure_retention_policy(
            self._failure_retention_policy(self.config)
        )
        self.thread_pool.start(
            ServiceTask(
                self.pipeline.apply_failure_retention,
                self.repository.protected_evidence_directories(),
            )
        )
        self.plc_poll_timer.setInterval(self.config.plc_poll_ms)
        self.plc_heartbeat_timer.setInterval(self.config.plc_heartbeat_ms)
        self.config.save(self.config_path)
        self.configuration_changed.emit(self.config)
        self._add_event("SETTINGS", "Application configuration updated")

    def _add_event(self, category: str, message: str, details: dict[str, Any] | None = None) -> None:
        event = {
            "category": category,
            "message": message,
            "details": details or {},
        }
        self.repository.add_audit_event(
            username=self.config.operator_name,
            category=category,
            message=message,
            details=details,
        )
        self.event_added.emit(event)

    def _begin_activity(self, key: str, reason: str) -> None:
        with self._activity_lock:
            self._activity.begin(key, reason)
            busy, busy_reason = self._activity.busy, self._activity.reason
        self._publish_activity_state(busy, busy_reason)

    def _end_activity(self, key: str) -> None:
        with self._activity_lock:
            self._activity.end(key)
            busy, busy_reason = self._activity.busy, self._activity.reason
        self._publish_activity_state(busy, busy_reason)

    def _publish_activity_state(
        self,
        busy: bool | None = None,
        busy_reason: str | None = None,
    ) -> None:
        if busy is None or busy_reason is None:
            with self._activity_lock:
                busy = self._activity.busy
                busy_reason = self._activity.reason
        self._busy = bool(busy)
        self._busy_reason = str(busy_reason)
        # Emit even when the boolean remains True so the header can refresh when
        # the highest-priority reason changes (for example CAMERA -> INSPECTING).
        self.busy_changed.emit(self._busy)

    def shutdown(self) -> None:
        self.plc_poll_timer.stop()
        self.plc_heartbeat_timer.stop()
        self.plc_reconnect_timer.stop()
        # Restores the saved profile if a technician left a preview running.
        self.stop_camera_preview(restore=True)
        try:
            if self.plc.connected and self._bypass_active:
                try:
                    self.plc.set_bypass(False)
                except Exception:  # noqa: S110 - shutdown clear is best-effort; the watchdog revokes bypass
                    # PLC watchdog logic must revoke effective bypass when the
                    # HMI heartbeat stops even if a normal shutdown clear fails.
                    pass
            self.camera.disconnect()
        finally:
            self.plc.disconnect()

    def defer_initialize(self) -> None:
        QTimer.singleShot(0, self.initialize)
