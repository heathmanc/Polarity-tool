from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.controller import AppController
from battery_inspector.services import CameraCapabilities
from battery_inspector.ui.palette import AMBER, BAD, NEUTRAL
from battery_inspector.ui.widgets import LabeledValue, PanelFrame


class DiagnosticsPage(QWidget):
    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("DIAGNOSTICS")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_button = QPushButton("REFRESH STATUS & CAMERA")
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        grid = QGridLayout()
        grid.setSpacing(10)
        root.addLayout(grid, 1)

        self.camera_panel = PanelFrame()
        camera_layout = QVBoxLayout(self.camera_panel)
        camera_layout.setContentsMargins(16, 14, 16, 14)
        camera_header = QHBoxLayout()
        camera_title = QLabel("CAMERA")
        camera_title.setObjectName("PanelTitle")
        self.camera_state = QLabel("CONNECTING")
        self.camera_state.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.camera_state.setStyleSheet(f"color: {AMBER}; font-weight: 800;")
        camera_header.addWidget(camera_title)
        camera_header.addStretch(1)
        camera_header.addWidget(self.camera_state)
        camera_layout.addLayout(camera_header)

        self.camera_description = QLabel(controller.camera.description)
        self.camera_description.setProperty("muted", True)
        self.camera_description.setWordWrap(True)
        camera_layout.addWidget(self.camera_description)

        self.camera_driver = LabeledValue(
            "Driver",
            controller.camera_driver_name,
        )
        self.camera_selection = LabeledValue(
            "Selection policy",
            "FIRST AVAILABLE — NO MODEL/SERIAL LOCK",
        )
        self.camera_model = LabeledValue("Detected model", "—")
        self.camera_serial = LabeledValue("Detected serial", "—")
        self.camera_transport = LabeledValue("Transport", "—")
        self.camera_sensor_resolution = LabeledValue("Detected sensor", "—")
        self.camera_max_resolution = LabeledValue("Maximum acquisition ROI", "—")
        self.camera_active_resolution = LabeledValue("Active acquisition ROI", "—")
        self.camera_exposure = LabeledValue("Exposure", "—")
        self.camera_gain = LabeledValue("Gain", "—")
        self.camera_pixel_format = LabeledValue("Pixel format", "—")
        self.camera_frame_rate = LabeledValue("Frame rate", "—")
        self.camera_trigger = LabeledValue("Trigger", "—")
        camera_metrics = (
            self.camera_driver,
            self.camera_selection,
            self.camera_model,
            self.camera_serial,
            self.camera_transport,
            self.camera_sensor_resolution,
            self.camera_max_resolution,
            self.camera_active_resolution,
            self.camera_exposure,
            self.camera_gain,
            self.camera_pixel_format,
            self.camera_frame_rate,
            self.camera_trigger,
        )
        camera_grid = QGridLayout()
        camera_grid.setHorizontalSpacing(18)
        camera_grid.setVerticalSpacing(0)
        for index, item in enumerate(camera_metrics):
            camera_grid.addWidget(item, index // 3, index % 3)
        for column in range(3):
            camera_grid.setColumnStretch(column, 1)
        camera_layout.addLayout(camera_grid)
        camera_layout.addStretch(1)
        grid.addWidget(self.camera_panel, 0, 0)

        self.plc_panel, self.plc_state, self.plc_description, self.plc_fields = self._service_panel(
            "PLC",
            controller.plc.description,
            [
                ("Driver", controller.plc_driver_name),
                ("Address", controller.config.plc_address),
                ("Poll interval", f"{controller.config.plc_poll_ms} ms"),
                ("Heartbeat interval", f"{controller.config.plc_heartbeat_ms} ms"),
                ("Heartbeat state", "—"),
                ("Bypass", "UNKNOWN"),
            ],
        )
        grid.addWidget(self.plc_panel, 0, 1)

        vision = PanelFrame()
        vision_layout = QVBoxLayout(vision)
        vision_layout.setContentsMargins(16, 14, 16, 14)
        vision_header = QHBoxLayout()
        vision_title = QLabel("VISION PIPELINE")
        vision_title.setObjectName("PanelTitle")
        self.vision_state = QLabel("NOT READY")
        self.vision_state.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.vision_state.setStyleSheet(f"color: {AMBER}; font-weight: 800;")
        vision_header.addWidget(vision_title)
        vision_header.addStretch(1)
        vision_header.addWidget(self.vision_state)
        vision_layout.addLayout(vision_header)
        vision_note = QLabel(
            "The active recipe must have a verified reference, validated battery registration, "
            "rotation-invariant polarity classification, and current physical validation evidence."
        )
        vision_note.setProperty("muted", True)
        vision_note.setWordWrap(True)
        vision_layout.addWidget(vision_note)
        self.vision_reference = LabeledValue("Active recipe reference", "—")
        self.vision_locator = LabeledValue("Battery localization", "—")
        self.vision_classifier = LabeledValue("Polarity classifier", "—")
        self.vision_ml_model = LabeledValue("Station ML model", "—")
        self.vision_ring = LabeledValue("Ring inspection", "OpenCV HSV preview")
        self.vision_issues = LabeledValue("Readiness issues", "—")
        vision_metrics = (
            self.vision_reference,
            self.vision_locator,
            self.vision_classifier,
            self.vision_ml_model,
            self.vision_ring,
            self.vision_issues,
        )
        vision_grid = QGridLayout()
        vision_grid.setHorizontalSpacing(18)
        vision_grid.setVerticalSpacing(0)
        for index, item in enumerate(vision_metrics):
            vision_grid.addWidget(item, index // 2, index % 2)
        vision_grid.setColumnStretch(0, 1)
        vision_grid.setColumnStretch(1, 1)
        vision_layout.addLayout(vision_grid)
        vision_layout.addStretch(1)
        grid.addWidget(vision, 1, 0)

        resources = PanelFrame()
        resources_layout = QVBoxLayout(resources)
        resources_layout.setContentsMargins(16, 14, 16, 14)
        resources_title = QLabel("SYSTEM RESOURCES")
        resources_title.setObjectName("PanelTitle")
        resources_layout.addWidget(resources_title)
        disk_label = QLabel("Inspection image storage")
        resources_layout.addWidget(disk_label)
        self.disk = QProgressBar()
        self.disk.setRange(0, 100)
        self.set_disk_usage(controller.health.get("disk", {}))
        resources_layout.addWidget(self.disk)
        db = LabeledValue("Database", str(controller.repository.database_path))
        resources_layout.addWidget(db)
        self.camera_source = LabeledValue(
            "Camera source",
            f"Configured {controller.config.camera_backend.upper()} | "
            f"Active {controller.camera_backend_active.upper()}",
        )
        resources_layout.addWidget(self.camera_source)
        self.plc_source = LabeledValue(
            "PLC source",
            f"Configured {controller.config.plc_backend.upper()} | Active {controller.plc_backend_active.upper()}",
        )
        resources_layout.addWidget(self.plc_source)
        resources_layout.addStretch(1)
        grid.addWidget(resources, 1, 1)

        self.refresh_button.clicked.connect(self.refresh)
        controller.camera_capabilities_changed.connect(self.set_camera_capabilities)
        controller.camera_discovery_changed.connect(self.set_camera_discovery)
        controller.camera_test_completed.connect(self.set_camera_test)
        controller.camera_operation_busy.connect(lambda busy: self.refresh_button.setEnabled(not busy))
        controller.plc_operation_busy.connect(lambda busy: self.refresh_button.setEnabled(not busy))
        controller.plc_test_completed.connect(self.set_plc_test)
        controller.plc_simulation_state_changed.connect(self.set_plc_live_state)
        controller.configuration_changed.connect(self.set_configuration)
        controller.active_recipe_changed.connect(lambda _recipe: self.refresh_vision())
        controller.recipes_changed.connect(lambda _recipes: self.refresh_vision())

        self.set_configuration(controller.config)
        self.refresh_vision()
        self.set_plc_live_state(controller.plc_simulation_state())
        if controller.camera_capabilities:
            self.set_camera_capabilities(controller.camera_capabilities)

    def _service_panel(self, title: str, description: str, fields: list[tuple[str, str]]):
        panel = PanelFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        header = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("PanelTitle")
        state = QLabel("CONNECTING")
        state.setAlignment(Qt.AlignmentFlag.AlignRight)
        state.setStyleSheet(f"color: {AMBER}; font-weight: 800;")
        header.addWidget(title_label)
        header.addStretch(1)
        header.addWidget(state)
        layout.addLayout(header)
        description_label = QLabel(description)
        description_label.setProperty("muted", True)
        description_label.setWordWrap(True)
        layout.addWidget(description_label)
        field_widgets: dict[str, LabeledValue] = {}
        for caption, value in fields:
            item = LabeledValue(caption, value)
            field_widgets[caption] = item
            layout.addWidget(item)
        layout.addStretch(1)
        return panel, state, description_label, field_widgets

    def refresh(self) -> None:
        self.set_health(self.controller.health)
        self.controller.discover_camera_hardware()

    def set_camera_discovery(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        devices = list(result.get("devices", []))
        self.camera_driver.set_value(self.controller.camera_driver_name)
        self.camera_source.set_value(
            f"Configured {self.controller.config.camera_backend.upper()} | "
            f"Active {self.controller.camera_backend_active.upper()}"
        )
        if not devices:
            self.camera_model.set_value("No camera detected", "bad")
            return
        first = devices[0]
        self.camera_model.set_value(first.model_name)
        self.camera_serial.set_value(first.serial_number or "Not reported")
        self.camera_transport.set_value(first.transport or first.device_class or "Not reported")

    def set_camera_capabilities(self, capabilities: object) -> None:
        if not isinstance(capabilities, CameraCapabilities):
            return
        self.camera_description.setText(self.controller.camera.description)
        self.camera_driver.set_value(self.controller.camera_driver_name)
        self.camera_source.set_value(
            f"Configured {self.controller.config.camera_backend.upper()} | "
            f"Active {self.controller.camera_backend_active.upper()}"
        )
        if capabilities.device:
            self.camera_model.set_value(capabilities.device.model_name)
            self.camera_serial.set_value(capabilities.device.serial_number or "Not reported")
            self.camera_transport.set_value(
                capabilities.device.transport or capabilities.device.device_class or "Not reported"
            )
        sensor_width, sensor_height = capabilities.maximum_resolution
        max_width, max_height = capabilities.maximum_acquisition_resolution
        active_width, active_height = capabilities.active_resolution
        self.camera_sensor_resolution.set_value(f"{sensor_width} x {sensor_height} px")
        self.camera_max_resolution.set_value(f"{max_width} x {max_height} px")
        self.camera_active_resolution.set_value(f"{active_width} x {active_height} px")
        exposure_mode = capabilities.current_exposure_auto or "Off"
        exposure_unit = capabilities.exposure_us.unit or "us"
        if exposure_mode == "Off":
            exposure_value = (
                f"{capabilities.exposure_us.current:.1f} {exposure_unit} — MANUAL"
            )
        else:
            exposure_value = (
                f"{exposure_mode.upper()} — "
                f"{capabilities.exposure_us.current:.1f} {exposure_unit}"
            )
        self.camera_exposure.set_value(exposure_value)
        gain_mode = capabilities.current_gain_auto or "Off"
        gain_unit = capabilities.gain_db.unit or "camera units"
        if gain_mode == "Off":
            gain_value = f"{capabilities.gain_db.current:.2f} {gain_unit} — MANUAL"
        else:
            gain_value = (
                f"{gain_mode.upper()} — "
                f"{capabilities.gain_db.current:.2f} {gain_unit}"
            )
        self.camera_gain.set_value(gain_value)
        self.camera_pixel_format.set_value(capabilities.current_pixel_format or "Camera default")
        if capabilities.frame_rate_hz.available:
            rate_prefix = "LIMITED" if capabilities.frame_rate_enabled else "CAMERA CONTROLLED"
            self.camera_frame_rate.set_value(
                f"{capabilities.frame_rate_hz.current:.2f} fps — {rate_prefix}"
            )
        else:
            self.camera_frame_rate.set_value("Not reported")
        self.camera_trigger.set_value(
            f"PLC TAG — {self.controller.config.tags.trigger}"
        )

    def set_camera_test(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        suffix = "TESTED"
        tone = "good"
        self.camera_active_resolution.set_value(
            f"{result['frame_width']} x {result['frame_height']} px — {suffix}",
            tone,
        )

    def set_plc_test(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        self.plc_description.setText(str(result.get("description", self.controller.plc.description)))
        self.plc_fields["Driver"].set_value(str(result.get("driver", self.controller.plc_driver_name)))
        backend = str(result.get("backend", self.controller.config.plc_backend))
        active_backend = str(result.get("active_backend", backend))
        address = (
            "INTERNAL SIMULATION"
            if active_backend == "simulation"
            else str(result.get("address", "—"))
        )
        self.plc_fields["Address"].set_value(address)
        self.plc_fields["Poll interval"].set_value(f"{result.get('poll_ms', self.controller.config.plc_poll_ms)} ms")
        self.plc_fields["Heartbeat interval"].set_value(
            f"{result.get('heartbeat_ms', self.controller.config.plc_heartbeat_ms)} ms"
        )
        self.plc_source.set_value(
            f"Configured {self.controller.config.plc_backend.upper()} | "
            f"Active {self.controller.plc_backend_active.upper()}"
        )

    def set_configuration(self, config) -> None:
        self.plc_description.setText(self.controller.plc.description)
        self.plc_fields["Driver"].set_value(self.controller.plc_driver_name)
        self.plc_fields["Address"].set_value(
            "INTERNAL SIMULATION" if config.plc_backend == "simulation" else config.plc_address
        )
        self.plc_fields["Poll interval"].set_value(f"{config.plc_poll_ms} ms")
        self.plc_fields["Heartbeat interval"].set_value(f"{config.plc_heartbeat_ms} ms")
        self.plc_source.set_value(
            f"Configured {config.plc_backend.upper()} | Active {self.controller.plc_backend_active.upper()}"
        )

    def set_plc_live_state(self, payload: object) -> None:
        state = dict(payload)  # type: ignore[arg-type]
        heartbeat = "1" if state.get("heartbeat") else "0"
        count = int(state.get("heartbeat_count", 0) or 0)
        last_ok = str(state.get("heartbeat_last_ok", "") or "—")
        self.plc_fields["Heartbeat state"].set_value(
            f"{heartbeat} | writes {count} | last OK {last_ok}",
            "good" if count else "warning",
        )
        known = bool(state.get("bypass_known", False))
        active = bool(state.get("bypass", False))
        if not known:
            self.plc_fields["Bypass"].set_value("UNKNOWN", "warning")
        elif active:
            self.plc_fields["Bypass"].set_value("ACTIVE", "warning")
        else:
            self.plc_fields["Bypass"].set_value("OFF")

    def refresh_vision(self) -> None:
        readiness = self.controller.inspection_readiness()
        ready = bool(readiness.get("ready"))
        self.vision_state.setText("READY" if ready else "NOT READY")
        self.vision_state.setStyleSheet(
            f"color: {NEUTRAL if ready else AMBER}; font-weight: 800;"
        )
        self.vision_reference.set_value(
            "CAPTURED" if readiness.get("recipe_has_reference") else "MISSING",
            None if readiness.get("recipe_has_reference") else "warning",
        )
        locator_status = str(readiness.get("locator_status", "UNKNOWN"))
        classifier_status = str(readiness.get("classifier_status", "UNKNOWN"))
        self.vision_locator.set_value(
            locator_status,
            None if not any(
                str(item).startswith("BATTERY_LOCATOR_NOT_READY")
                for item in readiness.get("issues", [])
            ) else "warning",
        )
        self.vision_classifier.set_value(
            classifier_status,
            None if not any(
                str(item).startswith("POLARITY_CLASSIFIER_NOT_READY")
                for item in readiness.get("issues", [])
            ) else "warning",
        )
        ml_info = self.controller.ml_model_info(require_runtime=False)
        if ml_info.get("ready"):
            self.vision_ml_model.set_value(
                f"{ml_info.get('model_id', '')} {ml_info.get('model_version', '')} "
                f"[{str(ml_info.get('model_sha256', ''))[:12]}]"
            )
        else:
            self.vision_ml_model.set_value("NOT LOADED", "warning")
        issues = [str(item) for item in readiness.get("issues", [])]
        self.vision_issues.set_value(
            "NONE" if not issues else "\n".join(issues),
            None if not issues else "warning",
        )

    def set_disk_usage(self, state: dict) -> None:
        """Render the measured free space on the station data volume."""

        if not state.get("measured"):
            self.disk.setValue(0)
            self.disk.setFormat("STORAGE NOT MEASURED")
            return
        used_percent = float(state.get("used_percent", 0.0))
        free_percent = float(state.get("free_percent", 0.0))
        self.disk.setValue(int(round(used_percent)))
        self.disk.setFormat(f"{used_percent:.0f}% used — {free_percent:.0f}% free")

    def set_health(self, health: dict) -> None:
        self.set_disk_usage(health.get("disk", {}))
        for key, label in (("camera", self.camera_state), ("plc", self.plc_state)):
            state = health.get(key, {"ok": False, "text": "UNKNOWN"})
            text = str(state.get("text", "UNKNOWN"))
            color = (
                AMBER
                if text == "SIMULATION"
                else (NEUTRAL if state["ok"] else BAD)
            )
            label.setText(state["text"])
            label.setStyleSheet(f"color: {color}; font-weight: 800;")
        self.refresh_vision()
