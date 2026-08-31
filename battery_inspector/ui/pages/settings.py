from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.config import (
    AppConfig,
    CameraConfig,
    MlConfig,
    PlcTagMap,
    ml_configuration_requires_apply,
)
from battery_inspector.controller import AppController
from battery_inspector.services import CameraCapabilities
from battery_inspector.ui.image_widgets import CropPreview, bgr_array_to_qimage
from battery_inspector.ui.palette import (
    AMBER,
    AMBER_BG,
    BAD,
    BORDER,
    GOOD,
    SURFACE_ALT,
    TEXT_MUTED,
    tone_color,
)
from battery_inspector.ui.widgets import LabeledValue, PanelFrame


class SettingsPage(QWidget):
    """Engineering settings page with technician-safe camera discovery.

    The camera serial is never entered manually. The default and technician-facing
    behavior is to use the first Basler camera returned by pylon and display its
    identity for verification.
    """

    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._last_capabilities: CameraCapabilities | None = controller.camera_capabilities
        self._updating_controls = False
        self._camera_busy = False
        self._plc_busy = False
        self._save_in_progress = False
        self._pending_save_config: AppConfig | None = None
        self._pending_save_steps: list[str] = []
        self._active_save_step: str | None = None
        self._ml_settings_touched = False
        self._station_transfer_busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("SETTINGS")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.save_state = QLabel("NO UNSAVED CHANGES")
        self.save_state.setProperty("muted", True)
        header.addWidget(self.save_state)
        self.save_button = QPushButton("SAVE & APPLY")
        self.save_button.setObjectName("PrimaryButton")
        header.addSpacing(12)
        header.addWidget(self.save_button)
        root.addLayout(header)

        panel = PanelFrame()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        panel_layout.addWidget(self.tabs)
        root.addWidget(panel, 1)

        self._build_general_tab()
        self._build_camera_tab()
        self._build_ml_tab()
        self._build_plc_tab()
        self._connect_dirty_tracking()

        # Writing to the camera on every spin-box tick would queue dozens of
        # applies behind a dragged control. One apply settles the queue after
        # the technician stops moving.
        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(220)
        self._preview_debounce.timeout.connect(self._push_preview_settings)
        for control in self._camera_image_controls():
            signal = (
                control.toggled
                if isinstance(control, QCheckBox)
                else (
                    control.currentIndexChanged
                    if isinstance(control, QComboBox)
                    else control.valueChanged
                )
            )
            signal.connect(self._preview_settings_changed)

        self.save_button.clicked.connect(self.save)
        controller.camera_discovery_changed.connect(self.set_camera_discovery)
        controller.camera_capabilities_changed.connect(self.set_camera_capabilities)
        controller.camera_test_completed.connect(self.camera_test_completed)
        controller.camera_operation_failed.connect(self.camera_operation_failed)
        controller.camera_operation_busy.connect(self.set_camera_operation_busy)
        controller.camera_operation_queued.connect(self.camera_operation_queued)
        controller.camera_preview_frame.connect(self._camera_preview_frame)
        controller.camera_preview_state.connect(self._camera_preview_state)
        controller.plc_test_completed.connect(self.plc_test_completed)
        controller.plc_operation_failed.connect(self.plc_operation_failed)
        controller.plc_operation_busy.connect(self.set_plc_operation_busy)
        controller.plc_simulation_state_changed.connect(self.set_plc_simulation_state)
        controller.health_changed.connect(self.set_plc_health)
        controller.ml_model_changed.connect(self.ml_model_configuration_changed)
        controller.station_transfer_completed.connect(self.station_transfer_completed)
        controller.station_transfer_failed.connect(self.station_transfer_failed)
        controller.station_transfer_busy.connect(self.set_station_transfer_busy)

        self.set_plc_health(controller.health)
        self.set_plc_simulation_state(controller.plc_simulation_state())
        if self._last_capabilities is not None:
            self.set_camera_capabilities(self._last_capabilities)
        self.set_ml_model_info(controller.ml_model_info(require_runtime=False))

    def _build_general_tab(self) -> None:
        self.general_tab = QWidget()
        general_form = QFormLayout(self.general_tab)

        self.camera_backend = QComboBox()
        self.camera_backend.addItem(
            "Auto — first Basler camera; demo fallback if unavailable",
            "auto",
        )
        self.camera_backend.addItem(
            "Basler required — first detected pylon camera",
            "basler",
        )
        self.camera_backend.addItem("Demo image — no camera hardware", "simulation")
        self._select_combo_data(self.camera_backend, self.controller.config.camera_backend)

        self.fullscreen = QCheckBox("Start HMI full screen")
        self.fullscreen.setChecked(self.controller.config.fullscreen)
        self.operator = QLineEdit(self.controller.config.operator_name)
        self.failure_retention_days = QSpinBox()
        self.failure_retention_days.setRange(0, 3650)
        self.failure_retention_days.setSpecialValueText("Disabled")
        self.failure_retention_days.setSuffix(" days")
        self.failure_retention_days.setValue(
            self.controller.config.failure_retention_days
        )
        self.failure_retention_max_gb = QDoubleSpinBox()
        self.failure_retention_max_gb.setDecimals(1)
        self.failure_retention_max_gb.setRange(0.0, 10_000.0)
        self.failure_retention_max_gb.setSingleStep(0.5)
        self.failure_retention_max_gb.setSpecialValueText("Disabled")
        self.failure_retention_max_gb.setSuffix(" GB")
        self.failure_retention_max_gb.setValue(
            self.controller.config.failure_retention_max_gb
        )
        self.validation_runs = QSpinBox()
        self.validation_runs.setRange(1, 50)
        self.validation_runs.setSuffix(" samples")
        self.validation_runs.setValue(self.controller.config.validation_runs_required)
        validation_note = QLabel(
            "How many independent samples a new recipe revision must pass before it can be "
            "activated. A sample counts when it is a different battery, confirmed in the "
            "wizard, or the same battery moved. Existing recipes keep the count they were "
            "validated against until they are revalidated."
        )
        validation_note.setWordWrap(True)
        validation_note.setProperty("muted", True)

        retention_note = QLabel(
            "Production PASS frames and records are never written. Non-PASS evidence is "
            "retained until either enabled limit is reached; the oldest failures are removed first."
        )
        retention_note.setWordWrap(True)
        retention_note.setProperty("muted", True)
        general_form.addRow("Camera source", self.camera_backend)
        general_form.addRow("Display", self.fullscreen)
        general_form.addRow("Current technician", self.operator)
        general_form.addRow("Recipe validation samples", self.validation_runs)
        general_form.addRow("Validation policy", validation_note)
        general_form.addRow("Failure retention age", self.failure_retention_days)
        general_form.addRow("Failure storage limit", self.failure_retention_max_gb)
        general_form.addRow("Storage policy", retention_note)

        transfer_panel = PanelFrame(subpanel=True)
        transfer_layout = QVBoxLayout(transfer_panel)
        transfer_layout.setContentsMargins(14, 12, 14, 12)
        transfer_title = QLabel("WORKSTATION BACKUP & RESTORE")
        transfer_title.setObjectName("PanelTitle")
        transfer_layout.addWidget(transfer_title)
        transfer_note = QLabel(
            "Export one verified ZIP before moving Pole Position to another PC. The ZIP includes settings, "
            "recipes, validation assets, ML data and models, audit history, and retained failure evidence. "
            "Import verifies and stages the ZIP; the restore is applied safely on the next application start."
        )
        transfer_note.setWordWrap(True)
        transfer_note.setProperty("muted", True)
        transfer_layout.addWidget(transfer_note)
        transfer_actions = QHBoxLayout()
        self.export_backup_button = QPushButton("EXPORT WORKSTATION BACKUP")
        self.import_backup_button = QPushButton("IMPORT WORKSTATION BACKUP")
        transfer_actions.addWidget(self.export_backup_button)
        transfer_actions.addWidget(self.import_backup_button)
        transfer_layout.addLayout(transfer_actions)
        self.station_transfer_status = QLabel("No backup or restore operation is in progress.")
        self.station_transfer_status.setWordWrap(True)
        self.station_transfer_status.setProperty("muted", True)
        transfer_layout.addWidget(self.station_transfer_status)
        general_form.addRow(transfer_panel)

        self.export_backup_button.clicked.connect(self.export_workstation_backup)
        self.import_backup_button.clicked.connect(self.import_workstation_backup)
        self.tabs.addTab(self.general_tab, "GENERAL")

    @staticmethod
    def _display_bytes(value: int) -> str:
        size = float(max(0, int(value)))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024.0 or unit == "TB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024.0
        return f"{size:.1f} TB"

    def export_workstation_backup(self) -> None:
        if self._station_transfer_busy:
            return
        if self.save_state.text() == "UNSAVED CHANGES":
            QMessageBox.information(
                self,
                "Save settings first",
                "Select SAVE & APPLY before exporting so the ZIP contains the current approved settings.",
            )
            return
        default_name = (
            self.controller.project_root
            / f"Pole_Position_Workstation_Backup_{datetime.now():%Y%m%d_%H%M%S}.zip"
        )
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Export Pole Position workstation backup",
            str(default_name),
            "Pole Position backups (*.zip)",
        )
        if not selected:
            return
        if not selected.lower().endswith(".zip"):
            selected += ".zip"
        answer = QMessageBox.question(
            self,
            "Create workstation backup",
            "Create a migration ZIP containing this station's settings, recipes, validation assets, "
            "ML data and models, audit history, and retained failure evidence?\n\n"
            "Production PASS images are not stored and therefore are not included.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.station_transfer_status.setText("CREATING AND VERIFYING WORKSTATION BACKUP...")
        self.station_transfer_status.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        if not self.controller.create_workstation_backup(Path(selected)):
            self.station_transfer_status.setText(
                "BACKUP NOT STARTED — wait until the station and current settings operation are idle."
            )
            self.station_transfer_status.setStyleSheet(f"color: {BAD}; font-weight: 700;")

    def import_workstation_backup(self) -> None:
        if self._station_transfer_busy:
            return
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Import Pole Position workstation backup",
            str(self.controller.project_root),
            "Pole Position backups (*.zip)",
        )
        if not selected:
            return
        answer = QMessageBox.warning(
            self,
            "Stage workstation restore",
            "The ZIP will be fully checked before any station data changes. If valid, Pole Position must "
            "restart to apply it. At restart, the current workstation is first saved to a rollback ZIP, "
            "then settings, recipes, validation assets, ML data, and retained evidence are replaced.\n\n"
            "Stage this restore?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.station_transfer_status.setText("VERIFYING AND STAGING WORKSTATION RESTORE...")
        self.station_transfer_status.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        if not self.controller.stage_workstation_restore(Path(selected)):
            self.station_transfer_status.setText(
                "RESTORE NOT STARTED — wait until the station and current settings operation are idle."
            )
            self.station_transfer_status.setStyleSheet(f"color: {BAD}; font-weight: 700;")

    def set_station_transfer_busy(self, busy: bool) -> None:
        self._station_transfer_busy = bool(busy)
        self._refresh_action_buttons()

    def station_transfer_completed(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        operation = str(result.get("operation", ""))
        if operation == "backup":
            path = str(result.get("path", ""))
            size = self._display_bytes(int(result.get("size_bytes", 0) or 0))
            digest = str(result.get("sha256", ""))
            self.station_transfer_status.setText(
                f"BACKUP COMPLETE — {size}; SHA-256 {digest[:16]}...; {path}"
            )
            self.station_transfer_status.setStyleSheet(f"color: {GOOD}; font-weight: 700;")
            QMessageBox.information(
                self,
                "Workstation backup complete",
                f"The verified migration ZIP was created successfully.\n\n{path}\n\n"
                f"Files: {result.get('file_count', 0)}\nSize: {size}\nSHA-256: {digest}",
            )
            return

        version = str(result.get("application_version", "") or "unknown")
        self.station_transfer_status.setText(
            f"RESTORE VERIFIED AND STAGED — source version {version}; restart required."
        )
        self.station_transfer_status.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        answer = QMessageBox.question(
            self,
            "Restore ready for restart",
            "The backup passed its manifest, path, size, SHA-256, configuration, and database checks. "
            "It will be applied before services start the next time Pole Position opens.\n\n"
            "Close Pole Position now?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            QTimer.singleShot(0, self.window().close)

    def station_transfer_failed(self, message: str) -> None:
        self.station_transfer_status.setText(f"BACKUP / RESTORE FAILED — {message}")
        self.station_transfer_status.setStyleSheet(f"color: {BAD}; font-weight: 700;")
        QMessageBox.critical(self, "Backup / restore failed", message)

    def _build_camera_tab(self) -> None:
        """Build camera settings as three fixed pages with no scrolling."""

        # CAMERA DEVICE -----------------------------------------------------
        device_tab = QWidget()
        device_root = QVBoxLayout(device_tab)
        device_root.setContentsMargins(10, 10, 10, 10)
        device_root.setSpacing(10)

        discovery = PanelFrame(subpanel=True)
        discovery_layout = QVBoxLayout(discovery)
        discovery_layout.setContentsMargins(14, 12, 14, 12)
        discovery_header = QHBoxLayout()
        discovery_title = QLabel("CAMERA DISCOVERY")
        discovery_title.setObjectName("PanelTitle")
        discovery_header.addWidget(discovery_title)
        discovery_header.addStretch(1)
        self.scan_camera_button = QPushButton("SCAN PHYSICAL CAMERAS")
        discovery_header.addWidget(self.scan_camera_button)
        discovery_layout.addLayout(discovery_header)

        auto_note = QLabel(
            "The station opens the first camera reported by pylon. Model and serial are displayed "
            "for maintenance verification only; recipes are not locked to a camera model or serial number."
        )
        auto_note.setWordWrap(True)
        auto_note.setProperty("muted", True)
        discovery_layout.addWidget(auto_note)

        identity_grid = QGridLayout()
        identity_grid.setHorizontalSpacing(24)
        identity_grid.setVerticalSpacing(2)
        self.camera_selection = LabeledValue("Selection", "FIRST AVAILABLE")
        self.camera_active_source = LabeledValue("Active source", self.controller.camera_driver_name)
        self.camera_model = LabeledValue("Detected model", "Not scanned")
        self.camera_serial_display = LabeledValue("Detected serial", "—")
        self.camera_transport = LabeledValue("Transport", "—")
        self.camera_count = LabeledValue("Detected devices", "—")
        self.camera_sensor_resolution = LabeledValue("Detected sensor", "—")
        self.camera_resolution = LabeledValue("Active acquisition ROI", "—")
        identity_grid.addWidget(self.camera_selection, 0, 0)
        identity_grid.addWidget(self.camera_active_source, 0, 1)
        identity_grid.addWidget(self.camera_count, 0, 2)
        identity_grid.addWidget(self.camera_model, 1, 0)
        identity_grid.addWidget(self.camera_serial_display, 1, 1)
        identity_grid.addWidget(self.camera_transport, 1, 2)
        identity_grid.addWidget(self.camera_sensor_resolution, 2, 0)
        identity_grid.addWidget(self.camera_resolution, 2, 1, 1, 2)
        discovery_layout.addLayout(identity_grid)

        self.detected_devices = QLabel("Press SCAN PHYSICAL CAMERAS to enumerate available devices.")
        self.detected_devices.setWordWrap(True)
        self.detected_devices.setProperty("muted", True)
        discovery_layout.addWidget(self.detected_devices)
        device_root.addWidget(discovery)

        device_guidance = PanelFrame(subpanel=True)
        device_guidance_layout = QVBoxLayout(device_guidance)
        device_guidance_layout.setContentsMargins(14, 12, 14, 12)
        guidance_title = QLabel("DEVICE SELECTION POLICY")
        guidance_title.setObjectName("PanelTitle")
        device_guidance_layout.addWidget(guidance_title)
        guidance = QLabel(
            "Connect one production camera per station whenever practical. If multiple Basler cameras are "
            "present, device 1 is selected and the HMI reports the condition for maintenance review."
        )
        guidance.setWordWrap(True)
        guidance.setProperty("muted", True)
        device_guidance_layout.addWidget(guidance)
        device_root.addWidget(device_guidance)
        device_root.addStretch(1)

        self.camera_tab = device_tab
        self.tabs.addTab(device_tab, "CAMERA DEVICE")

        # CAMERA IMAGE ------------------------------------------------------
        image_tab = QWidget()
        image_root = QGridLayout(image_tab)
        image_root.setContentsMargins(10, 10, 10, 10)
        image_root.setSpacing(10)

        acquisition = PanelFrame(subpanel=True)
        acquisition_layout = QVBoxLayout(acquisition)
        acquisition_layout.setContentsMargins(14, 12, 14, 12)
        acquisition_title = QLabel("EXPOSURE & GAIN")
        acquisition_title.setObjectName("PanelTitle")
        acquisition_layout.addWidget(acquisition_title)
        acquisition_form = QFormLayout()

        self.exposure_auto = QComboBox()
        self._populate_auto_combo(self.exposure_auto, self.controller.config.camera.exposure_auto)
        self.exposure = QDoubleSpinBox()
        self.exposure.setDecimals(1)
        self.exposure.setRange(0.0, 10_000_000.0)
        self.exposure.setSingleStep(100.0)
        self.exposure.setValue(self.controller.config.camera.exposure_us)
        self.exposure.setSuffix(" us")

        self.gain_auto = QComboBox()
        self._populate_auto_combo(self.gain_auto, self.controller.config.camera.gain_auto)
        self.gain = QDoubleSpinBox()
        self.gain.setDecimals(2)
        self.gain.setRange(-100.0, 100.0)
        self.gain.setSingleStep(0.1)
        self.gain.setValue(self.controller.config.camera.gain_db)
        self.gain.setSuffix(" dB")

        acquisition_form.addRow("Exposure mode", self.exposure_auto)
        acquisition_form.addRow("Exposure time", self.exposure)
        acquisition_form.addRow("Gain mode", self.gain_auto)
        acquisition_form.addRow("Gain", self.gain)
        acquisition_layout.addLayout(acquisition_form)
        self.exposure_range = QLabel(
            "Detected exposure and gain limits will appear after camera discovery."
        )
        self.exposure_range.setWordWrap(True)
        self.exposure_range.setProperty("muted", True)
        acquisition_layout.addWidget(self.exposure_range)
        acquisition_layout.addStretch(1)
        image_root.addWidget(acquisition, 0, 0)  # column 0, row 0

        image_format = PanelFrame(subpanel=True)
        image_layout = QVBoxLayout(image_format)
        image_layout.setContentsMargins(14, 12, 14, 12)
        image_title = QLabel("IMAGE FORMAT")
        image_title.setObjectName("PanelTitle")
        image_layout.addWidget(image_title)
        image_form = QFormLayout()

        self.resolution_mode = QComboBox()
        self.resolution_mode.addItem("Keep camera current / default", "CameraDefault")
        self.resolution_mode.addItem("Use maximum detected acquisition ROI", "Maximum")
        self.resolution_mode.addItem("Use custom acquisition ROI", "Custom")
        self._select_combo_data(
            self.resolution_mode,
            self.controller.config.camera.resolution_mode,
        )
        # Not self.width / self.height: those are QWidget methods, and
        # binding a spin box over them makes any call to widget.width() or
        # widget.height() on this page raise instead of returning a size.
        self.frame_width = QSpinBox()
        self.frame_width.setRange(1, 100_000)
        self.frame_width.setValue(max(1, self.controller.config.camera.width or 1))
        self.frame_width.setSuffix(" px")
        self.frame_height = QSpinBox()
        self.frame_height.setRange(1, 100_000)
        self.frame_height.setValue(max(1, self.controller.config.camera.height or 1))
        self.frame_height.setSuffix(" px")
        self.center_roi = QCheckBox("Center a custom acquisition ROI automatically")
        self.center_roi.setChecked(self.controller.config.camera.center_roi)
        self.offset_x = QSpinBox()
        self.offset_x.setRange(0, 100_000)
        self.offset_x.setValue(self.controller.config.camera.offset_x)
        self.offset_x.setSuffix(" px")
        self.offset_y = QSpinBox()
        self.offset_y.setRange(0, 100_000)
        self.offset_y.setValue(self.controller.config.camera.offset_y)
        self.offset_y.setSuffix(" px")
        self.pixel_format = QComboBox()
        self.pixel_format.addItem("Camera default", "")
        if self.controller.config.camera.pixel_format:
            self.pixel_format.addItem(
                self.controller.config.camera.pixel_format,
                self.controller.config.camera.pixel_format,
            )
            self.pixel_format.setCurrentIndex(1)
        self.camera_timeout = QSpinBox()
        self.camera_timeout.setRange(250, 30_000)
        self.camera_timeout.setValue(self.controller.config.camera.timeout_ms)
        self.camera_timeout.setSuffix(" ms")

        image_form.addRow("Resolution mode", self.resolution_mode)
        image_form.addRow("Width", self.frame_width)
        image_form.addRow("Height", self.frame_height)
        image_form.addRow("ROI placement", self.center_roi)
        image_form.addRow("Offset X", self.offset_x)
        image_form.addRow("Offset Y", self.offset_y)
        image_form.addRow("Pixel format", self.pixel_format)
        image_form.addRow("Grab timeout", self.camera_timeout)
        image_layout.addLayout(image_form)
        self.resolution_range = QLabel(
            "Detected width, height, and increment limits will appear after discovery."
        )
        self.resolution_range.setWordWrap(True)
        self.resolution_range.setProperty("muted", True)
        image_layout.addWidget(self.resolution_range)
        image_root.addWidget(image_format, 0, 1, 2, 1)  # column 1, both rows

        # COLOUR & TONE ------------------------------------------------------
        colour = PanelFrame(subpanel=True)
        colour_layout = QVBoxLayout(colour)
        colour_layout.setContentsMargins(14, 12, 14, 12)
        colour_title = QLabel("COLOUR & TONE")
        colour_title.setObjectName("PanelTitle")
        colour_layout.addWidget(colour_title)
        colour_form = QFormLayout()

        self.balance_white_auto = QComboBox()
        self._populate_auto_combo(
            self.balance_white_auto, self.controller.config.camera.balance_white_auto
        )
        self.balance_red = self._ratio_spin(self.controller.config.camera.balance_ratio_red)
        self.balance_green = self._ratio_spin(self.controller.config.camera.balance_ratio_green)
        self.balance_blue = self._ratio_spin(self.controller.config.camera.balance_ratio_blue)

        self.black_level_enabled = QCheckBox("Set the black level")
        self.black_level_enabled.setChecked(self.controller.config.camera.black_level_enabled)
        self.black_level = QDoubleSpinBox()
        self.black_level.setDecimals(2)
        self.black_level.setRange(-1000.0, 1000.0)
        self.black_level.setSingleStep(0.5)
        self.black_level.setValue(self.controller.config.camera.black_level)

        self.gamma_enabled = QCheckBox("Set gamma")
        self.gamma_enabled.setChecked(self.controller.config.camera.gamma_enabled)
        self.gamma = QDoubleSpinBox()
        self.gamma.setDecimals(2)
        self.gamma.setRange(0.01, 4.0)
        self.gamma.setSingleStep(0.05)
        self.gamma.setValue(self.controller.config.camera.gamma)

        colour_form.addRow("White balance mode", self.balance_white_auto)
        colour_form.addRow("Red ratio", self.balance_red)
        colour_form.addRow("Green ratio", self.balance_green)
        colour_form.addRow("Blue ratio", self.balance_blue)
        colour_form.addRow("Black level", self.black_level_enabled)
        colour_form.addRow("Black level value", self.black_level)
        colour_form.addRow("Gamma", self.gamma_enabled)
        colour_form.addRow("Gamma value", self.gamma)
        colour_layout.addLayout(colour_form)

        self.colour_note = QLabel(
            "The silver/brass check compares colour against the recipe reference. "
            "Change white balance and every reference must be recaptured."
        )
        self.colour_note.setWordWrap(True)
        self.colour_note.setStyleSheet(
            f"color: {AMBER}; background: {AMBER_BG}; border: 1px solid {AMBER}; padding: 6px;"
        )
        colour_layout.addWidget(self.colour_note)
        colour_layout.addStretch(1)
        image_root.addWidget(colour, 1, 0)  # column 0, row 1

        # LIVE PREVIEW -------------------------------------------------------
        live = PanelFrame(subpanel=True)
        live_layout = QVBoxLayout(live)
        live_layout.setContentsMargins(14, 12, 14, 12)
        live_title = QLabel("LIVE PREVIEW")
        live_title.setObjectName("PanelTitle")
        live_layout.addWidget(live_title)
        self.live_preview = CropPreview()
        self.live_preview.setMinimumHeight(180)
        live_layout.addWidget(self.live_preview, 1)
        self.live_preview_status = QLabel("STOPPED")
        self.live_preview_status.setWordWrap(True)
        self.live_preview_status.setProperty("muted", True)
        live_layout.addWidget(self.live_preview_status)
        live_buttons = QHBoxLayout()
        self.live_preview_button = QPushButton("START LIVE PREVIEW")
        self.live_preview_button.clicked.connect(self._toggle_live_preview)
        live_buttons.addWidget(self.live_preview_button)
        live_layout.addLayout(live_buttons)
        image_root.addWidget(live, 0, 2, 2, 1)  # column 2, both rows

        image_root.setColumnStretch(0, 1)
        image_root.setColumnStretch(1, 1)
        image_root.setColumnStretch(2, 1)

        self.camera_image_tab = image_tab
        self.tabs.addTab(image_tab, "CAMERA IMAGE")

        # CAMERA I/O & TEST -------------------------------------------------
        io_tab = QWidget()
        io_root = QGridLayout(io_tab)
        io_root.setContentsMargins(10, 10, 10, 10)
        io_root.setSpacing(10)

        acquisition_control = PanelFrame(subpanel=True)
        acq_layout = QVBoxLayout(acquisition_control)
        acq_layout.setContentsMargins(14, 12, 14, 12)
        acq_title = QLabel("ACQUISITION CONTROL")
        acq_title.setObjectName("PanelTitle")
        acq_layout.addWidget(acq_title)
        acq_form = QFormLayout()
        self.acquisition_mode = QComboBox()
        self.acquisition_mode.addItem("Triggered snapshot — one exposure per cycle", "On")
        self.acquisition_mode.addItem("Free run — camera exposes continuously", "Off")
        self._select_combo_data(
            self.acquisition_mode,
            self.controller.config.camera.trigger_mode,
        )
        self.frame_rate_enabled = QCheckBox("Limit acquisition frame rate")
        self.frame_rate_enabled.setChecked(self.controller.config.camera.frame_rate_enabled)
        self.frame_rate = QDoubleSpinBox()
        self.frame_rate.setDecimals(2)
        self.frame_rate.setRange(0.1, 1000.0)
        self.frame_rate.setValue(self.controller.config.camera.frame_rate_fps)
        self.frame_rate.setSuffix(" fps")
        self.trigger_mode = QComboBox()
        self.trigger_mode.addItem(
            f"PLC tag — {self.controller.config.tags.trigger}",
            "PLC_TAG",
        )
        acq_form.addRow("Acquisition", self.acquisition_mode)
        acq_form.addRow("Frame-rate control", self.frame_rate_enabled)
        acq_form.addRow("Frame rate", self.frame_rate)
        acq_form.addRow("Production trigger", self.trigger_mode)
        acq_layout.addLayout(acq_form)
        self.acquisition_range = QLabel()
        self.acquisition_range.setWordWrap(True)
        self.acquisition_range.setProperty("muted", True)
        acq_layout.addWidget(self.acquisition_range)
        acq_layout.addStretch(1)
        io_root.addWidget(acquisition_control, 0, 0)

        preview_panel = PanelFrame(subpanel=True)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(14, 12, 14, 12)
        preview_title = QLabel("LAST CAMERA TEST FRAME")
        preview_title.setObjectName("PanelTitle")
        preview_layout.addWidget(preview_title)
        self.camera_preview = CropPreview()
        self.camera_preview.setMinimumHeight(330)
        preview_layout.addWidget(self.camera_preview, 1)
        preview_note = QLabel(
            "The preview is display-sized. Inspection retains the camera's full active resolution."
        )
        preview_note.setWordWrap(True)
        preview_note.setProperty("muted", True)
        preview_layout.addWidget(preview_note)
        io_root.addWidget(preview_panel, 0, 1)

        test_panel = PanelFrame(subpanel=True)
        test_layout = QHBoxLayout(test_panel)
        test_layout.setContentsMargins(14, 12, 14, 12)
        self.camera_status = QLabel("Camera settings have not been tested in this session.")
        self.camera_status.setWordWrap(True)
        self.camera_status.setProperty("muted", True)
        test_layout.addWidget(self.camera_status, 1)
        self.apply_camera_button = QPushButton("APPLY & TEST CAMERA")
        self.apply_camera_button.setObjectName("PrimaryButton")
        test_layout.addWidget(self.apply_camera_button)
        io_root.addWidget(test_panel, 1, 0, 1, 2)
        io_root.setColumnStretch(0, 1)
        io_root.setColumnStretch(1, 2)
        io_root.setRowStretch(0, 1)

        self.camera_io_tab = io_tab
        self.tabs.addTab(io_tab, "CAMERA I/O")

        self.scan_camera_button.clicked.connect(self.scan_cameras)
        self.apply_camera_button.clicked.connect(self.apply_camera_only)
        self.resolution_mode.currentIndexChanged.connect(self.update_resolution_controls)
        self.center_roi.toggled.connect(self.update_resolution_controls)
        self.exposure_auto.currentIndexChanged.connect(self.update_auto_controls)
        self.gain_auto.currentIndexChanged.connect(self.update_auto_controls)
        self.frame_rate_enabled.toggled.connect(self.update_acquisition_controls)
        self.acquisition_mode.currentIndexChanged.connect(self.update_acquisition_controls)
        self.update_resolution_controls()
        self.update_auto_controls()
        self.update_acquisition_controls()

    def _build_plc_tab(self) -> None:
        """Build PLC mode and tag pages without vertical scrolling."""

        mode_tab = QWidget()
        mode_root = QGridLayout(mode_tab)
        mode_root.setContentsMargins(10, 10, 10, 10)
        mode_root.setSpacing(10)

        mode_panel = PanelFrame(subpanel=True)
        mode_layout = QVBoxLayout(mode_panel)
        mode_layout.setContentsMargins(14, 12, 14, 12)
        title = QLabel("PLC MODE")
        title.setObjectName("PanelTitle")
        mode_layout.addWidget(title)

        mode_form = QFormLayout()
        self.plc_mode = QComboBox()
        self.plc_mode.addItem("Simulation — no physical PLC required", "simulation")
        self.plc_mode.addItem("pycomm3 — Allen-Bradley Logix PLC", "pycomm3")
        self._select_combo_data(self.plc_mode, self.controller.config.plc_backend)
        self.plc_active_source = LabeledValue("Active source", self.controller.plc_driver_name)
        self.plc_connection_state = LabeledValue("Connection", "CONNECTING")
        mode_form.addRow("Requested source", self.plc_mode)
        mode_form.addRow(self.plc_active_source)
        mode_form.addRow(self.plc_connection_state)
        mode_layout.addLayout(mode_form)

        self.plc_mode_note = QLabel(
            "Simulation keeps the complete HMI, recipe workflow, polling, and trigger handshake active "
            "without opening a network connection."
        )
        self.plc_mode_note.setWordWrap(True)
        self.plc_mode_note.setProperty("warning", True)
        mode_layout.addWidget(self.plc_mode_note)
        mode_layout.addStretch(1)
        mode_root.addWidget(mode_panel, 0, 0)

        simulator_panel = PanelFrame(subpanel=True)
        simulator_layout = QVBoxLayout(simulator_panel)
        simulator_layout.setContentsMargins(14, 12, 14, 12)
        simulator_title = QLabel("PLC SIMULATOR / COMMISSIONING")
        simulator_title.setObjectName("PanelTitle")
        simulator_layout.addWidget(simulator_title)
        simulator_note = QLabel(
            "Use these controls without a physical PLC. A test trigger uses the same inspection, Busy, "
            "Complete, Pass, and Fail BOOL path used by pycomm3."
        )
        simulator_note.setWordWrap(True)
        simulator_note.setProperty("muted", True)
        simulator_layout.addWidget(simulator_note)

        simulator_grid = QGridLayout()
        self.sim_trigger_state = LabeledValue("Trigger", "OFF")
        self.sim_busy_state = LabeledValue("Busy", "OFF")
        self.sim_complete_state = LabeledValue("Complete", "OFF")
        self.sim_result_state = LabeledValue("Last result", "NONE")
        self.sim_pass_state = LabeledValue("Pass output", "OFF")
        self.sim_fail_state = LabeledValue("Fail output", "OFF")
        self.sim_recipe_state = LabeledValue("Recipe", "—")
        self.sim_heartbeat_state = LabeledValue("HMI heartbeat", "OFF")
        self.sim_bypass_state = LabeledValue("Bypass", "OFF")
        simulator_grid.addWidget(self.sim_trigger_state, 0, 0)
        simulator_grid.addWidget(self.sim_busy_state, 0, 1)
        simulator_grid.addWidget(self.sim_complete_state, 0, 2)
        simulator_grid.addWidget(self.sim_pass_state, 1, 0)
        simulator_grid.addWidget(self.sim_fail_state, 1, 1)
        simulator_grid.addWidget(self.sim_result_state, 1, 2)
        simulator_grid.addWidget(self.sim_recipe_state, 2, 0)
        simulator_grid.addWidget(self.sim_heartbeat_state, 2, 1)
        simulator_grid.addWidget(self.sim_bypass_state, 2, 2)
        simulator_layout.addLayout(simulator_grid)

        self.plc_status = QLabel("PLC settings have not been tested in this session.")
        self.plc_status.setWordWrap(True)
        self.plc_status.setProperty("muted", True)
        simulator_layout.addWidget(self.plc_status)
        simulator_actions = QHBoxLayout()
        self.simulate_plc_trigger_button = QPushButton("SEND ONE TEST TRIGGER")
        self.apply_plc_button = QPushButton("APPLY & TEST PLC")
        self.apply_plc_button.setObjectName("PrimaryButton")
        simulator_actions.addWidget(self.simulate_plc_trigger_button)
        simulator_actions.addWidget(self.apply_plc_button)
        simulator_layout.addLayout(simulator_actions)
        mode_root.addWidget(simulator_panel, 0, 1)

        connection_panel = PanelFrame(subpanel=True)
        connection_layout = QVBoxLayout(connection_panel)
        connection_layout.setContentsMargins(14, 12, 14, 12)
        connection_title = QLabel("CONNECTION")
        connection_title.setObjectName("PanelTitle")
        connection_layout.addWidget(connection_title)
        connection_form = QFormLayout()
        self.plc_address = QLineEdit(self.controller.config.plc_address)
        self.plc_poll = QSpinBox()
        self.plc_poll.setRange(50, 5000)
        self.plc_poll.setValue(self.controller.config.plc_poll_ms)
        self.plc_poll.setSuffix(" ms")
        self.plc_heartbeat = QSpinBox()
        self.plc_heartbeat.setRange(250, 10000)
        self.plc_heartbeat.setSingleStep(250)
        self.plc_heartbeat.setValue(self.controller.config.plc_heartbeat_ms)
        self.plc_heartbeat.setSuffix(" ms")
        connection_form.addRow("Logix path", self.plc_address)
        connection_form.addRow("Poll interval", self.plc_poll)
        connection_form.addRow("HMI heartbeat interval", self.plc_heartbeat)
        connection_layout.addLayout(connection_form)
        heartbeat_note = QLabel(
            "The HMI toggles the configured heartbeat BOOL independently of inspection activity. "
            "Recommended PLC watchdog: require the tag to change within 3x the heartbeat interval."
        )
        heartbeat_note.setWordWrap(True)
        heartbeat_note.setProperty("muted", True)
        connection_layout.addWidget(heartbeat_note)
        mode_root.addWidget(connection_panel, 1, 0, 1, 2)
        mode_root.setColumnStretch(0, 1)
        mode_root.setColumnStretch(1, 2)
        mode_root.setRowStretch(0, 1)

        self.plc_tab = mode_tab
        self.tabs.addTab(mode_tab, "PLC MODE")

        tags_tab = QWidget()
        tags_root = QVBoxLayout(tags_tab)
        tags_root.setContentsMargins(10, 10, 10, 10)
        tags_root.setSpacing(10)
        tag_panel = PanelFrame(subpanel=True)
        tag_layout = QVBoxLayout(tag_panel)
        tag_layout.setContentsMargins(14, 12, 14, 12)
        tag_title = QLabel("PYCOMM3 TAG MAP")
        tag_title.setObjectName("PanelTitle")
        tag_layout.addWidget(tag_title)
        tag_note = QLabel(
            "These fields are used only in pycomm3 mode. Simulation uses the same logical handshake internally. "
            "Set the acknowledge tag only when the PLC program raises it after reading a result; "
            "the station then clears Busy, Complete, Pass, and Fail together. Left blank, a result "
            "stays on the tags until the next cycle starts."
        )
        tag_note.setWordWrap(True)
        tag_note.setProperty("muted", True)
        tag_layout.addWidget(tag_note)

        selector_form = QFormLayout()
        self.plc_recipe_source = QComboBox()
        self.plc_recipe_source.addItem(
            "PLC selector tag — the PLC names the product every trigger",
            "plc",
        )
        self.plc_recipe_source.addItem(
            "Station selection — the recipe chosen on the Recipes page",
            "station",
        )
        self._select_combo_data(
            self.plc_recipe_source,
            self.controller.config.plc_recipe_source,
        )
        selector_form.addRow("Recipe source", self.plc_recipe_source)
        self.plc_recipe_selector = QComboBox()
        self.plc_recipe_selector.addItem(
            "Recipe name — Logix STRING / word value",
            "name",
        )
        self.plc_recipe_selector.addItem(
            "Recipe number — SINT / INT / DINT value",
            "number",
        )
        self._select_combo_data(
            self.plc_recipe_selector,
            self.controller.config.plc_recipe_selector,
        )
        selector_form.addRow("Recipe selector value", self.plc_recipe_selector)
        self.plc_recipe_source_note = QLabel()
        self.plc_recipe_source_note.setWordWrap(True)
        self.plc_recipe_source_note.setProperty("muted", True)
        selector_form.addRow("", self.plc_recipe_source_note)
        self.plc_recipe_source.currentIndexChanged.connect(self._update_recipe_source)
        self._update_recipe_source()
        tag_layout.addLayout(selector_form)

        tag_columns = QHBoxLayout()
        left_form = QFormLayout()
        right_form = QFormLayout()
        self.tag_edits: dict[str, QLineEdit] = {}
        fields = list(PlcTagMap.__dataclass_fields__)
        split = (len(fields) + 1) // 2
        for index, field_name in enumerate(fields):
            edit = QLineEdit(getattr(self.controller.config.tags, field_name))
            if field_name == "acknowledge":
                # Blank is a working configuration, not an unfinished one, so
                # the field says what blank does rather than looking unset.
                edit.setPlaceholderText("Blank — results stay latched until the next cycle")
            self.tag_edits[field_name] = edit
            target = left_form if index < split else right_form
            label = {
                "recipe_name": "Recipe selector tag",
                "acknowledge": "Acknowledge tag (optional)",
            }.get(field_name, field_name.replace("_", " ").title())
            target.addRow(label, edit)
        tag_columns.addLayout(left_form, 1)
        tag_columns.addSpacing(24)
        tag_columns.addLayout(right_form, 1)
        tag_layout.addLayout(tag_columns)
        tag_layout.addStretch(1)
        tags_root.addWidget(tag_panel, 1)

        self.plc_tags_tab = tags_tab
        self.tabs.addTab(tags_tab, "PLC TAGS")

        self.plc_mode.currentIndexChanged.connect(self.update_plc_controls)
        self.apply_plc_button.clicked.connect(self.apply_plc_only)
        self.simulate_plc_trigger_button.clicked.connect(self.simulate_plc_trigger)
        self.tag_edits["trigger"].textChanged.connect(self._update_trigger_mode_label)
        self._update_trigger_mode_label()
        self.update_plc_controls()

    def _build_ml_tab(self) -> None:
        """Build the station-wide ONNX polarity-model deployment page."""

        tab = QWidget()
        root = QGridLayout(tab)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        package = PanelFrame(subpanel=True)
        package_layout = QVBoxLayout(package)
        package_layout.setContentsMargins(14, 12, 14, 12)
        title = QLabel("POLARITY ML MODEL PACKAGE")
        title.setObjectName("PanelTitle")
        package_layout.addWidget(title)
        note = QLabel(
            "The production classifier runs an ONNX image model on the isolated metal terminal top. "
            "It does not see the red ring or molded case polarity symbols."
        )
        note.setWordWrap(True)
        note.setProperty("muted", True)
        package_layout.addWidget(note)

        model_form = QFormLayout()
        self.ml_model_path = QLineEdit(self.controller.config.ml.model_path)
        self.ml_manifest_path = QLineEdit(self.controller.config.ml.manifest_path)
        model_form.addRow("ONNX model", self.ml_model_path)
        model_form.addRow("Model manifest", self.ml_manifest_path)
        package_layout.addLayout(model_form)

        model_actions = QHBoxLayout()
        self.ml_browse_model = QPushButton("BROWSE MODEL")
        self.ml_browse_manifest = QPushButton("BROWSE MANIFEST")
        self.ml_apply_button = QPushButton("APPLY & TEST ML MODEL")
        self.ml_apply_button.setObjectName("PrimaryButton")
        model_actions.addWidget(self.ml_browse_model)
        model_actions.addWidget(self.ml_browse_manifest)
        model_actions.addStretch(1)
        model_actions.addWidget(self.ml_apply_button)
        package_layout.addLayout(model_actions)

        transfer_actions = QHBoxLayout()
        self.ml_export_package = QPushButton("EXPORT MODEL PACKAGE")
        self.ml_import_package = QPushButton("IMPORT MODEL PACKAGE")
        transfer_actions.addWidget(self.ml_export_package)
        transfer_actions.addWidget(self.ml_import_package)
        transfer_actions.addStretch(1)
        package_layout.addLayout(transfer_actions)
        transfer_note = QLabel(
            "A model package is a checksummed ZIP holding this station's ONNX model "
            "and its manifest, for moving one trained model to another station "
            "without moving the whole workstation."
        )
        transfer_note.setWordWrap(True)
        transfer_note.setProperty("muted", True)
        package_layout.addWidget(transfer_note)

        self.ml_use_new_revisions = QCheckBox(
            "Use the installed ML model for new and edited recipe revisions"
        )
        self.ml_use_new_revisions.setChecked(
            self.controller.config.ml.use_for_new_revisions
        )
        package_layout.addWidget(self.ml_use_new_revisions)
        root.addWidget(package, 0, 0)

        status_panel = PanelFrame(subpanel=True)
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(14, 12, 14, 12)
        status_title = QLabel("MODEL STATUS")
        status_title.setObjectName("PanelTitle")
        status_layout.addWidget(status_title)
        grid = QGridLayout()
        self.ml_state = LabeledValue("Runtime state", "NOT CONFIGURED")
        self.ml_model_id = LabeledValue("Model ID", "—")
        self.ml_model_version = LabeledValue("Model version", "—")
        self.ml_model_hash = LabeledValue("SHA-256", "—")
        self.ml_model_classes = LabeledValue("Classes", "—")
        self.ml_input_size = LabeledValue("Input", "—")
        grid.addWidget(self.ml_state, 0, 0)
        grid.addWidget(self.ml_model_id, 0, 1)
        grid.addWidget(self.ml_model_version, 0, 2)
        grid.addWidget(self.ml_model_hash, 1, 0)
        grid.addWidget(self.ml_model_classes, 1, 1)
        grid.addWidget(self.ml_input_size, 1, 2)
        status_layout.addLayout(grid)
        self.ml_status = QLabel()
        self.ml_status.setWordWrap(True)
        self.ml_status.setProperty("muted", True)
        status_layout.addWidget(self.ml_status)
        status_layout.addStretch(1)
        root.addWidget(status_panel, 0, 1)

        workflow = PanelFrame(subpanel=True)
        workflow_layout = QVBoxLayout(workflow)
        workflow_layout.setContentsMargins(14, 12, 14, 12)
        workflow_title = QLabel("DEPLOYMENT / RECIPE POLICY")
        workflow_title.setObjectName("PanelTitle")
        workflow_layout.addWidget(workflow_title)
        workflow_text = QLabel(
            "A recipe revision is bound to the exact model ID and SHA-256 used during guided validation. "
            "Replacing this station model will make ML-bound recipes NOT READY until a new revision is "
            "validated. Existing legacy recipes continue using their configured legacy classifier until edited.\n\n"
            "Use the dedicated ML TRAINING page for guided camera capture, adjustable terminal-top ROIs, class labeling, "
            "train/validation/test preparation, model training, held-out evaluation, and candidate installation. "
            "This page remains available for loading or verifying an externally trained ONNX package."
        )
        workflow_text.setWordWrap(True)
        workflow_text.setProperty("muted", True)
        workflow_layout.addWidget(workflow_text)
        workflow_layout.addStretch(1)
        root.addWidget(workflow, 1, 0, 1, 2)
        root.setColumnStretch(0, 3)
        root.setColumnStretch(1, 2)
        root.setRowStretch(0, 2)
        root.setRowStretch(1, 1)

        self.tabs.addTab(tab, "VISION / ML")
        self.ml_browse_model.clicked.connect(self.browse_ml_model)
        self.ml_browse_manifest.clicked.connect(self.browse_ml_manifest)
        self.ml_export_package.clicked.connect(self.export_ml_model_package)
        self.ml_import_package.clicked.connect(self.import_ml_model_package)
        self.ml_apply_button.clicked.connect(self.apply_ml_only)

    def export_ml_model_package(self) -> None:
        """Package the model this station is inspecting with."""

        info = self.controller.ml_model_info(require_runtime=False)
        default_name = (
            f"{info.get('model_id', 'polarity-model') or 'polarity-model'}_"
            f"{info.get('model_version', 'model') or 'model'}_package.zip"
        )
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Export ML model package",
            str(self.controller.project_root / default_name),
            "Pole Position model package (*.zip)",
        )
        if not selected:
            return
        try:
            result = self.controller.export_model_package(Path(selected))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        manifest = result.get("manifest", {})
        QMessageBox.information(
            self,
            "Model package written",
            f"{manifest.get('model_id', '')} {manifest.get('model_version', '')} was "
            f"written to:\n{result.get('path', '')}\n\n"
            f"SHA-256: {manifest.get('model_sha256', '')}\n\n"
            "A recipe revision stays bound to the model hash it was validated "
            "against, so installing this on another station makes recipes bound to "
            "this hash resolvable there — it does not revalidate anything.",
        )

    def import_ml_model_package(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Import ML model package",
            str(self.controller.project_root),
            "Pole Position model package (*.zip)",
        )
        if not selected:
            return
        answer = QMessageBox.question(
            self,
            "Import model package",
            "Verify this package and install it as this station's ML model?\n\n"
            "Recipe revisions bound to a different model hash keep failing closed "
            "until they are revalidated against the installed model.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.controller.import_model_package(Path(selected))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        manifest = result.get("manifest", {})
        # The controller emits ml_model_changed on install, which this page is
        # already connected to, so the status panel refreshes itself.
        QMessageBox.information(
            self,
            "Model installed",
            f"{manifest.get('model_id', '')} {manifest.get('model_version', '')} is "
            f"now this station's model.\n\nSHA-256: {result.get('model_sha256', '')}",
        )

    def browse_ml_model(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Select polarity ONNX model",
            self.ml_model_path.text() or str(self.controller.project_root),
            "ONNX models (*.onnx);;All files (*)",
        )
        if not selected:
            return
        self.ml_model_path.setText(selected)
        sidecar = Path(selected).with_suffix(".json")
        if sidecar.is_file():
            self.ml_manifest_path.setText(str(sidecar))

    def browse_ml_manifest(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Select polarity model manifest",
            self.ml_manifest_path.text() or str(self.controller.project_root),
            "JSON manifests (*.json);;All files (*)",
        )
        if selected:
            self.ml_manifest_path.setText(selected)

    def set_ml_model_info(self, payload: object) -> None:
        info = dict(payload)  # type: ignore[arg-type]
        ready = bool(info.get("ready"))
        runtime_checked = bool(info.get("runtime_checked"))
        if ready and runtime_checked:
            state_text = "READY"
            tone = "good"
        elif ready:
            state_text = "PACKAGE VERIFIED"
            tone = "warning"
        else:
            state_text = "NOT READY"
            tone = "warning"
        self.ml_state.set_value(state_text, tone)
        self.ml_model_id.set_value(str(info.get("model_id", "") or "—"), tone)
        self.ml_model_version.set_value(
            str(info.get("model_version", "") or "—"), tone
        )
        model_hash = str(info.get("model_sha256", "") or "")
        self.ml_model_hash.set_value(model_hash[:16] + "…" if model_hash else "—")
        classes = list(info.get("classes", []) or [])
        self.ml_model_classes.set_value(", ".join(classes).upper() if classes else "—")
        input_size = list(info.get("input_size", []) or [])
        self.ml_input_size.set_value(
            " x ".join(str(item) for item in input_size) if input_size else "—"
        )
        issues = [str(item) for item in info.get("issues", [])]
        if ready and runtime_checked:
            status_text = "MODEL PACKAGE AND ONNX RUNTIME VERIFIED — recipe revisions may bind to this model."
        elif ready:
            status_text = "MODEL PACKAGE VERIFIED — select APPLY & TEST ML MODEL to verify ONNX Runtime before binding a recipe."
        else:
            status_text = "; ".join(issues) or "No ONNX model package has been verified."
        self.ml_status.setText(status_text)
        self.ml_status.setStyleSheet(
            f"color: {tone_color(tone)}; font-weight: 700;"
        )

    def ml_model_configuration_changed(self, payload: object) -> None:
        """Refresh ML controls after training/install changes station config.

        The Settings page is created at application startup and can remain open
        in the page stack while ML Training installs a model.  Keep its paths
        synchronized so a later PLC-only save cannot restore a stale default
        model path.
        """

        configured = self.controller.config.ml.normalized()
        self._updating_controls = True
        try:
            self.ml_model_path.setText(configured.model_path)
            self.ml_manifest_path.setText(configured.manifest_path)
            self.ml_use_new_revisions.setChecked(
                configured.use_for_new_revisions
            )
        finally:
            self._updating_controls = False
        self._ml_settings_touched = False
        self.set_ml_model_info(payload)

    def apply_ml_only(self) -> None:
        if self._save_in_progress:
            return
        try:
            info = self.controller.apply_ml_configuration(
                model_path=self.ml_model_path.text().strip(),
                manifest_path=self.ml_manifest_path.text().strip(),
                use_for_new_revisions=self.ml_use_new_revisions.isChecked(),
            )
        except Exception as exc:  # noqa: BLE001
            self.set_ml_model_info(
                {"ready": False, "issues": [str(exc)]}
            )
            QMessageBox.critical(self, "ML model could not be loaded", str(exc))
            return
        self._ml_settings_touched = False
        self.set_ml_model_info(info)
        self.save_state.setText("ML MODEL VERIFIED AND SAVED")
        self.save_state.setStyleSheet(f"color: {GOOD}; font-weight: 700;")

    def _connect_dirty_tracking(self) -> None:
        widgets = [
            self.camera_backend,
            self.plc_mode,
            self.plc_recipe_selector,
            self.plc_recipe_source,
            self.fullscreen,
            self.operator,
            self.failure_retention_days,
            self.failure_retention_max_gb,
            self.exposure_auto,
            self.exposure,
            self.gain_auto,
            self.gain,
            self.resolution_mode,
            self.frame_width,
            self.frame_height,
            self.center_roi,
            self.offset_x,
            self.offset_y,
            self.pixel_format,
            self.camera_timeout,
            self.acquisition_mode,
            self.frame_rate_enabled,
            self.frame_rate,
            self.plc_address,
            self.plc_poll,
            self.plc_heartbeat,
            *self.tag_edits.values(),
        ]

        def connect_widget(widget, callback) -> None:
            for signal_name in (
                "textChanged",
                "valueChanged",
                "currentIndexChanged",
                "toggled",
                "stateChanged",
            ):
                signal = getattr(widget, signal_name, None)
                if signal is not None:
                    try:
                        signal.connect(callback)
                    except Exception:  # noqa: S110 - widgets expose different signals; a missing one is not an error
                        pass

        for widget in widgets:
            connect_widget(widget, self._mark_dirty)
        for widget in (
            self.ml_model_path,
            self.ml_manifest_path,
            self.ml_use_new_revisions,
        ):
            connect_widget(widget, self._mark_ml_dirty)

    def _mark_ml_dirty(self, *_args) -> None:
        if self._updating_controls:
            return
        self._ml_settings_touched = True
        self._mark_dirty()

    def _mark_dirty(self, *_args) -> None:
        if self._updating_controls:
            return
        self.save_state.setText("UNSAVED CHANGES")
        self.save_state.setStyleSheet(f"color: {AMBER}; font-weight: 700;")

    @staticmethod
    def _populate_auto_combo(combo: QComboBox, requested: str) -> None:
        combo.addItem("Camera default", "CameraDefault")
        combo.addItem("Manual", "Off")
        combo.addItem("Auto once", "Once")
        combo.addItem("Auto continuous", "Continuous")
        SettingsPage._select_combo_data(combo, requested)

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _combo_value(combo: QComboBox) -> str:
        return str(combo.currentData() or combo.currentText())

    def _update_recipe_source(self) -> None:
        """Say what each choice means, and grey out what it makes irrelevant."""

        plc_names_it = self._combo_value(self.plc_recipe_source) == "plc"
        self.plc_recipe_selector.setEnabled(plc_names_it)
        if plc_names_it:
            self.plc_recipe_source_note.setText(
                "The selector tag decides the recipe on every trigger. A tag that "
                "names nothing, or names a product with no validated revision, is "
                "refused: no cycle is run, no other recipe is substituted, and the "
                "Ready tag goes false. Use this for any line that runs more than "
                "one product, and for headless operation."
            )
        else:
            self.plc_recipe_source_note.setText(
                "PLC triggers grade against the recipe selected on the Recipes "
                "page, and the selector tag is not read for product identity. Use "
                "this on the bench, in simulation, and on a single-product station "
                "whose PLC program carries no selector tag."
            )

    def update_resolution_controls(self) -> None:
        custom = self._combo_value(self.resolution_mode) == "Custom"
        self.center_roi.setEnabled(custom)
        self.frame_width.setEnabled(custom)
        self.frame_height.setEnabled(custom)
        offsets_enabled = custom and not self.center_roi.isChecked()
        self.offset_x.setEnabled(offsets_enabled)
        self.offset_y.setEnabled(offsets_enabled)

    def update_auto_controls(self) -> None:
        self.exposure.setEnabled(self._combo_value(self.exposure_auto) == "Off")
        self.gain.setEnabled(self._combo_value(self.gain_auto) == "Off")

    def update_acquisition_controls(self) -> None:
        """Frame rate is a free-run setting. Say so instead of implying otherwise."""

        triggered = self._combo_value(self.acquisition_mode) == "On"
        can_limit = not triggered and getattr(self, "_frame_rate_enable_available", True)
        self.frame_rate_enabled.setEnabled(can_limit)
        self.frame_rate.setEnabled(can_limit and self.frame_rate_enabled.isChecked())
        detected = getattr(self, "_detected_acquisition", "")
        if triggered:
            self.acquisition_range.setText(
                "The station executes a software trigger and the camera exposes on "
                "demand, so every cycle grades an exposure taken for that cycle. "
                "Frame rate does not apply: nothing is exposed until the station "
                "asks. Production inspections start only from the configured PLC "
                "Trigger tag; the Overview page provides the only manual action."
            )
        else:
            self.acquisition_range.setText(
                "The camera exposes continuously and a cycle discards one frame "
                "boundary before grading the next completed exposure, so the frame "
                "rate sets how long a cycle waits for its frame -- about two frame "
                "periods at worst. Triggered snapshot avoids that wait entirely."
            )
        if detected:
            self.acquisition_range.setText(
                f"{self.acquisition_range.text()}\n\n{detected}"
            )

    def _update_trigger_mode_label(self, *_args) -> None:
        tag = self.tag_edits["trigger"].text().strip() or "NOT CONFIGURED"
        self.trigger_mode.blockSignals(True)
        self.trigger_mode.clear()
        self.trigger_mode.addItem(f"PLC tag — {tag}", "PLC_TAG")
        self.trigger_mode.blockSignals(False)

    def update_plc_controls(self) -> None:
        hardware = self._combo_value(self.plc_mode) == "pycomm3"
        self.plc_address.setEnabled(hardware)
        for edit in self.tag_edits.values():
            edit.setEnabled(hardware)
        self.plc_recipe_source.setEnabled(True)
        self._update_recipe_source()
        if hardware:
            self.apply_plc_button.setText("APPLY & TEST PYCOMM3")
            self.plc_poll.setEnabled(True)
            self.plc_mode_note.setText(
                "pycomm3 mode opens the configured Logix path and verifies the trigger and recipe tags. "
                "If verification fails, the currently active PLC service remains in use."
            )
            self.plc_mode_note.setStyleSheet(
                f"color: {TEXT_MUTED}; padding: 10px; background: {SURFACE_ALT}; border: 1px solid {BORDER};"
            )
        else:
            self.apply_plc_button.setText("APPLY & TEST SIMULATION")
            self.plc_poll.setEnabled(True)
            self.plc_mode_note.setText(
                "Simulation keeps the complete HMI, recipe workflow, PLC polling, and trigger handshake active "
                "without opening a network connection. The footer and header remain explicitly marked PLC SIMULATION."
            )
            self.plc_mode_note.setStyleSheet(
                f"color: {AMBER}; padding: 10px; background: {AMBER_BG}; border: 1px solid {AMBER};"
            )
        self._refresh_action_buttons()

    def scan_cameras(self) -> None:
        self.set_camera_status("SCANNING FOR BASLER CAMERAS...", "warning")
        if not self.controller.discover_camera_hardware():
            self.set_camera_status(
                "CAMERA SCAN NOT STARTED — CAMERA IS IN USE BY STARTUP, AN INSPECTION, OR ANOTHER CAMERA OPERATION",
                "warning",
            )

    def set_camera_discovery(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        devices = list(result.get("devices", []))
        error = str(result.get("error", "")).strip()
        probe_error = str(result.get("probe_error", "")).strip()
        active_backend = str(result.get("active_backend", self.controller.camera_backend_active))
        active_labels = {
            "basler": "PYPYLON — PHYSICAL CAMERA",
            "basler_defaults": "PYPYLON — CAMERA DEFAULTS; VERIFY SETTINGS",
            "simulation": "DEMO IMAGE",
            "simulation_fallback": "DEMO FALLBACK — PHYSICAL CAMERA UNAVAILABLE",
            "starting": "STARTING",
        }
        self.camera_active_source.set_value(
            active_labels.get(active_backend, active_backend.upper() or "UNKNOWN"),
            "warning"
            if active_backend in {"simulation", "simulation_fallback", "basler_defaults"}
            else "good",
        )
        count_tone = "good" if len(devices) == 1 else ("warning" if devices else "bad")
        self.camera_count.set_value(str(len(devices)), count_tone)
        if not devices:
            no_hardware_tone = "warning" if active_backend in {"simulation", "simulation_fallback"} else "bad"
            self.camera_selection.set_value("FIRST AVAILABLE — NONE FOUND", no_hardware_tone)
            self.camera_model.set_value("No physical camera detected", no_hardware_tone)
            self.camera_serial_display.set_value("—")
            self.camera_transport.set_value("—")
            self.detected_devices.setText(
                f"No Basler camera was detected.\n{error}" if error else "No Basler camera was detected."
            )
            if active_backend == "simulation_fallback":
                self.set_camera_status(
                    "NO PHYSICAL CAMERA DETECTED — DEMO FALLBACK REMAINS ACTIVE",
                    "warning",
                )
            elif active_backend == "simulation":
                self.set_camera_status(
                    "NO PHYSICAL CAMERA DETECTED — DEMO MODE IS ACTIVE",
                    "warning",
                )
            else:
                self.set_camera_status("NO PHYSICAL CAMERA DETECTED", "bad")
            return

        lines = []
        for index, device in enumerate(devices):
            marker = "AUTO SELECTED" if index == 0 else "AVAILABLE"
            lines.append(f"{marker}: {device.display_name}")
        if len(devices) > 1:
            lines.append(
                "NOTICE: Multiple cameras are connected. The station will use device 1, the first device "
                "returned by pylon. The displayed model and serial are verification information only."
            )
        if probe_error:
            lines.append(
                "NOTICE: Device identity was detected, but capabilities could not be opened: "
                f"{probe_error}"
            )
        self.detected_devices.setText("\n".join(lines))
        first = devices[0]
        self.camera_selection.set_value(f"FIRST AVAILABLE — DEVICE 1 OF {len(devices)}", "good")
        self.camera_model.set_value(first.model_name, "good")
        self.camera_serial_display.set_value(first.serial_number or "Not reported")
        self.camera_transport.set_value(first.transport or first.device_class or "Not reported")
        if probe_error:
            status = f"FIRST CAMERA DETECTED BUT NOT OPENED: {first.display_name}"
            tone = "warning"
        elif active_backend == "basler":
            status = f"FIRST CAMERA IS ACTIVE: {first.display_name}"
            tone = "good"
        elif active_backend == "basler_defaults":
            status = (
                f"FIRST CAMERA IS ACTIVE WITH CAMERA DEFAULTS: {first.display_name}. "
                "Review the detected ranges, then APPLY & TEST CAMERA to verify this station profile."
            )
            tone = "warning"
        else:
            status = (
                f"FIRST CAMERA DETECTED: {first.display_name}. "
                "APPLY & TEST CAMERA WILL ACTIVATE IT."
            )
            tone = "good"
        self.set_camera_status(status, tone)

    def set_camera_capabilities(self, capabilities: object) -> None:
        if not isinstance(capabilities, CameraCapabilities):
            return
        self._updating_controls = True
        try:
            self._apply_detected_camera_capabilities(capabilities)
        finally:
            self._updating_controls = False

    def _apply_detected_camera_capabilities(self, caps: CameraCapabilities) -> None:
        self._last_capabilities = caps
        self.camera_active_source.set_value(
            self.controller.camera_driver_name,
            "warning"
            if self.controller.camera_backend_active
            in {"simulation", "simulation_fallback", "basler_defaults"}
            else "good",
        )
        if caps.device:
            self.camera_model.set_value(caps.device.model_name, "good")
            self.camera_serial_display.set_value(caps.device.serial_number or "Not reported")
            self.camera_transport.set_value(caps.device.transport or caps.device.device_class or "Not reported")

        sensor_width, sensor_height = caps.maximum_resolution
        max_width, max_height = caps.maximum_acquisition_resolution
        active_width, active_height = caps.active_resolution
        self.camera_sensor_resolution.set_value(f"{sensor_width} x {sensor_height} px")
        self.camera_resolution.set_value(f"{active_width} x {active_height} px", "good")
        self.resolution_range.setText(
            "Detected sensor: "
            f"{sensor_width} x {sensor_height} px | Maximum acquisition ROI: "
            f"{max_width} x {max_height} px | "
            f"Width {int(caps.width.minimum)}..{int(caps.width.maximum)} step "
            f"{max(1, int(caps.width.increment or 1))} | "
            f"Height {int(caps.height.minimum)}..{int(caps.height.maximum)} step "
            f"{max(1, int(caps.height.increment or 1))}"
        )

        self._configure_integer_control(self.frame_width, caps.width, active_width)
        self._configure_integer_control(self.frame_height, caps.height, active_height)
        self._configure_integer_control(self.offset_x, caps.offset_x, int(caps.offset_x.current))
        self._configure_integer_control(self.offset_y, caps.offset_y, int(caps.offset_y.current))

        if caps.exposure_us.available:
            self.exposure.setSuffix(f" {caps.exposure_us.unit or 'us'}")
            self.exposure.setRange(caps.exposure_us.minimum, caps.exposure_us.maximum)
            self.exposure.setSingleStep(max(caps.exposure_us.increment, 1.0))
            requested = self.controller.config.camera.exposure_us or caps.exposure_us.current
            self.exposure.setValue(min(max(requested, caps.exposure_us.minimum), caps.exposure_us.maximum))
        if caps.gain_db.available:
            self.gain.setSuffix(f" {caps.gain_db.unit or 'camera units'}")
            self.gain.setRange(caps.gain_db.minimum, caps.gain_db.maximum)
            self.gain.setSingleStep(max(caps.gain_db.increment, 0.01))
            requested_gain = self.controller.config.camera.gain_db
            self.gain.setValue(min(max(requested_gain, caps.gain_db.minimum), caps.gain_db.maximum))

        exposure_unit = caps.exposure_us.unit or "us"
        gain_unit = caps.gain_db.unit or "camera units"
        self.exposure_range.setText(
            f"Exposure capability: {caps.exposure_us.minimum:.1f}.."
            f"{caps.exposure_us.maximum:.1f} {exposure_unit} | "
            f"Gain capability: {caps.gain_db.minimum:.2f}.."
            f"{caps.gain_db.maximum:.2f} {gain_unit}"
        )
        self._set_auto_modes(
            self.exposure_auto,
            caps.exposure_auto_modes,
            self.controller.config.camera.exposure_auto,
        )
        self._set_auto_modes(
            self.gain_auto,
            caps.gain_auto_modes,
            self.controller.config.camera.gain_auto,
        )
        self._set_pixel_formats(caps.pixel_formats)

        if caps.frame_rate_hz.available:
            self.frame_rate.setRange(caps.frame_rate_hz.minimum, caps.frame_rate_hz.maximum)
            self.frame_rate.setSingleStep(max(caps.frame_rate_hz.increment, 0.1))
            requested_rate = self.controller.config.camera.frame_rate_fps
            self.frame_rate.setValue(
                min(max(requested_rate, caps.frame_rate_hz.minimum), caps.frame_rate_hz.maximum)
            )
        self._frame_rate_enable_available = bool(caps.frame_rate_enable_available)
        frame_range = (
            f"{caps.frame_rate_hz.minimum:.2f}..{caps.frame_rate_hz.maximum:.2f} fps"
            if caps.frame_rate_hz.available
            else "not reported"
        )
        self._detected_acquisition = (
            f"Detected frame-rate capability: {frame_range} | "
            f"Production request: PLC tag {self.controller.config.tags.trigger}"
        )
        self.update_resolution_controls()
        self.update_auto_controls()
        self.update_acquisition_controls()

    @staticmethod
    def _configure_integer_control(control: QSpinBox, capability, fallback: int) -> None:
        if not capability.available:
            return
        minimum = int(round(capability.minimum))
        maximum = int(round(capability.maximum))
        control.setRange(minimum, max(minimum, maximum))
        control.setSingleStep(max(1, int(round(capability.increment or 1))))
        current = control.value()
        if current < minimum or current > maximum or current == 1:
            control.setValue(min(max(fallback, minimum), maximum))

    @staticmethod
    def _set_auto_modes(combo: QComboBox, modes: tuple[str, ...], requested: str) -> None:
        available = list(modes) if modes else ["Off", "Once", "Continuous"]
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Camera default", "CameraDefault")
        labels = {"Off": "Off — manual", "Once": "Once", "Continuous": "Continuous"}
        for value in available:
            combo.addItem(labels.get(value, value), value)
        index = combo.findData(requested)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    @staticmethod
    def _set_plain_combo(combo: QComboBox, values: tuple[str, ...], requested: str) -> None:
        cleaned = [value for value in values if value]
        if not cleaned:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(cleaned)
        index = combo.findText(requested, Qt.MatchFlag.MatchFixedString)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _set_pixel_formats(self, formats: tuple[str, ...]) -> None:
        requested = self.controller.config.camera.pixel_format
        self.pixel_format.blockSignals(True)
        self.pixel_format.clear()
        self.pixel_format.addItem("Camera default", "")
        for value in formats:
            self.pixel_format.addItem(value, value)
        if requested:
            index = self.pixel_format.findData(requested)
            if index < 0:
                self.pixel_format.addItem(requested, requested)
                index = self.pixel_format.count() - 1
            self.pixel_format.setCurrentIndex(index)
        self.pixel_format.blockSignals(False)

    def set_camera_operation_busy(self, busy: bool) -> None:
        self._camera_busy = busy
        if busy:
            self.set_camera_status("CAMERA OPERATION IN PROGRESS...", "warning")
        self._refresh_action_buttons()
        if not busy and self._save_in_progress and self._active_save_step is None:
            QTimer.singleShot(0, self._run_next_save_step)

    def camera_operation_queued(self, message: str) -> None:
        self.set_camera_status(
            f"CAMERA SETTINGS QUEUED — {message}",
            "warning",
        )

    def camera_test_completed(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        backend = str(result.get("camera_backend", ""))
        self.camera_active_source.set_value(
            str(result.get("camera_description", self.controller.camera_driver_name)),
            "warning" if backend in {"simulation", "simulation_fallback", "basler_defaults"} else "good",
        )
        backend_note = ""
        if backend == "simulation_fallback":
            backend_note = " — DEMO FALLBACK; NO PHYSICAL CAMERA ACTIVE"
        elif backend == "simulation":
            backend_note = " — DEMO IMAGE SOURCE"
        elif backend == "basler":
            backend_note = " — PHYSICAL BASLER CAMERA"
        elif backend == "basler_defaults":
            backend_note = " — PHYSICAL CAMERA USING DEFAULTS; VERIFY BEFORE PRODUCTION"
        preview_frame = result.get("preview_frame")
        if preview_frame is not None:
            try:
                self.camera_preview.set_pixmap_source(
                    QPixmap.fromImage(bgr_array_to_qimage(preview_frame))
                )
            except (TypeError, ValueError):
                self.camera_preview.setText("TEST FRAME COULD NOT BE DISPLAYED")
        if result.get("test_skipped"):
            self.set_camera_status(
                "SETTINGS APPLIED — "
                f"{result['frame_width']} x {result['frame_height']} px. "
                f"{result.get('test_message', 'Test capture skipped.')}{backend_note}",
                "warning",
            )
            self.save_state.setText("SETTINGS SAVED — EXTERNAL TRIGGER NOT TESTED")
            self.save_state.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
            self._operation_step_succeeded("camera")
            return
        self.set_camera_status(
            "TEST PASSED — "
            f"{result['frame_width']} x {result['frame_height']} px, "
            f"{result['channels']} channel(s), mean level {result['mean_level']:.1f}{backend_note}",
            "warning"
            if backend in {"simulation", "simulation_fallback", "basler_defaults"}
            else "good",
        )
        if backend in {"simulation", "simulation_fallback"}:
            self.save_state.setText("SETTINGS SAVED — SIMULATION IMAGE VERIFIED")
            self.save_state.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        elif backend == "basler_defaults":
            self.save_state.setText("CAMERA DEFAULTS ACTIVE — VERIFY PROFILE")
            self.save_state.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        else:
            self.save_state.setText("SETTINGS SAVED AND VERIFIED")
            self.save_state.setStyleSheet(f"color: {GOOD}; font-weight: 700;")
        self._operation_step_succeeded("camera")

    def camera_operation_failed(self, message: str) -> None:
        self.set_camera_status(f"CAMERA TEST FAILED — {message}", "bad")
        self.save_state.setText("NOT SAVED — CAMERA SETTINGS FAILED VALIDATION")
        self.save_state.setStyleSheet(f"color: {BAD}; font-weight: 700;")
        self._abort_save("CAMERA SETTINGS FAILED")
        QMessageBox.critical(self, "Camera operation failed", message)

    def set_plc_operation_busy(self, busy: bool) -> None:
        self._plc_busy = busy
        if busy:
            self.set_plc_status("PLC OPERATION IN PROGRESS...", "warning")
        self._refresh_action_buttons()
        if not busy and self._save_in_progress and self._active_save_step is None:
            QTimer.singleShot(0, self._run_next_save_step)

    def set_plc_health(self, health: dict) -> None:
        state = health.get("plc", {"ok": False, "text": "UNKNOWN"})
        ok = bool(state.get("ok"))
        text = str(state.get("text", "UNKNOWN"))
        tone = "warning" if text == "SIMULATION" else ("good" if ok else "bad")
        self.plc_connection_state.set_value(text, tone)
        self.plc_active_source.set_value(self.controller.plc_driver_name, tone)
        self._refresh_action_buttons()

    def set_plc_simulation_state(self, payload: object) -> None:
        state = dict(payload)  # type: ignore[arg-type]
        trigger = bool(state.get("trigger"))
        busy = bool(state.get("busy"))
        complete = bool(state.get("complete"))
        passed = state.get("passed")
        failed = bool(state.get("fail", False))
        selector = str(state.get("recipe_selector", self.controller.config.plc_recipe_selector))
        recipe_name = (
            str(state.get("recipe_number", "") or "—")
            if selector == "number"
            else str(state.get("recipe_name", "") or "—")
        )
        heartbeat = bool(state.get("heartbeat"))
        bypass = bool(state.get("bypass"))

        self.sim_trigger_state.set_value("ON" if trigger else "OFF", "warning" if trigger else "neutral")
        self.sim_busy_state.set_value("ON" if busy else "OFF", "warning" if busy else "neutral")
        self.sim_complete_state.set_value("ON" if complete else "OFF", "good" if complete else "neutral")
        if passed is True:
            result_text, result_tone = "PASS", "good"
        elif failed and complete:
            result_text, result_tone = "FAIL", "bad"
        else:
            result_text, result_tone = "NONE", "neutral"
        self.sim_result_state.set_value(result_text, result_tone)
        self.sim_pass_state.set_value("ON" if passed is True else "OFF", "good" if passed is True else "neutral")
        self.sim_fail_state.set_value("ON" if failed else "OFF", "bad" if failed else "neutral")
        self.sim_recipe_state.set_value(recipe_name)
        self.sim_heartbeat_state.set_value("ON" if heartbeat else "OFF")
        self.sim_bypass_state.set_value("ACTIVE" if bypass else "OFF", "warning" if bypass else "neutral")

        self._refresh_action_buttons()

    def plc_test_completed(self, payload: object) -> None:
        result = dict(payload)  # type: ignore[arg-type]
        backend = str(result.get("backend", self.controller.config.plc_backend))
        active_backend = str(result.get("active_backend", backend))
        simulated = active_backend == "simulation"
        self._updating_controls = True
        try:
            self._select_combo_data(self.plc_mode, backend)
            self._select_combo_data(
                self.plc_recipe_selector,
                self.controller.config.plc_recipe_selector,
            )
            self._select_combo_data(
                self.plc_recipe_source,
                self.controller.config.plc_recipe_source,
            )
        finally:
            self._updating_controls = False
        self.update_plc_controls()
        self.plc_active_source.set_value(
            str(result.get("description", self.controller.plc_driver_name)),
            "warning" if simulated else "good",
        )
        self.plc_connection_state.set_value(
            "SIMULATION" if simulated else "CONNECTED",
            "warning" if simulated else "good",
        )
        if simulated:
            self.set_plc_status(
                "SIMULATION ACTIVE — no physical PLC connection will be attempted. "
                "Use SEND ONE TEST TRIGGER to exercise the cycle handshake.",
                "warning",
            )
            self.save_state.setText("SETTINGS SAVED — PLC SIMULATION ACTIVE")
            self.save_state.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        else:
            state = dict(result.get("cycle_state", {}))
            selector = self.controller.config.plc_recipe_selector
            recipe = (
                str(state.get("recipe_number", "") or "not reported")
                if selector == "number"
                else str(state.get("recipe_name", "") or "not reported")
            )
            self.set_plc_status(
                f"PYCOMM3 CONNECTION VERIFIED — recipe {selector}: {recipe}; "
                f"poll {result.get('poll_ms', self.plc_poll.value())} ms; "
                f"heartbeat {result.get('heartbeat_ms', self.plc_heartbeat.value())} ms",
                "good",
            )
            self.save_state.setText("SETTINGS SAVED — PLC VERIFIED")
            self.save_state.setStyleSheet(f"color: {GOOD}; font-weight: 700;")
        self._operation_step_succeeded("plc")

    def plc_operation_failed(self, message: str) -> None:
        self.set_plc_status(
            f"PLC TEST FAILED — {message}. The previous PLC source remains active.",
            "bad",
        )
        self.save_state.setText("NOT SAVED — PLC SETTINGS FAILED VALIDATION")
        self.save_state.setStyleSheet(f"color: {BAD}; font-weight: 700;")
        self._abort_save("PLC SETTINGS FAILED")
        QMessageBox.critical(self, "PLC operation failed", message)

    def set_plc_status(self, text: str, tone: str = "neutral") -> None:
        self.plc_status.setText(text)
        self.plc_status.setStyleSheet(
            f"color: {tone_color(tone)}; font-weight: 700;"
        )

    def simulate_plc_trigger(self) -> None:
        if self.controller.pulse_simulated_plc_trigger():
            self.set_plc_status(
                "SIMULATED PLC TRIGGER SENT — the normal PLC poll will start one inspection cycle.",
                "warning",
            )
        else:
            self.set_plc_status(
                "SIMULATED TRIGGER NOT SENT — apply PLC Simulation first.",
                "bad",
            )

    def set_camera_status(self, text: str, tone: str = "neutral") -> None:
        self.camera_status.setText(text)
        self.camera_status.setStyleSheet(
            f"color: {tone_color(tone)}; font-weight: 700;"
        )

    def _camera_config_from_controls(self) -> CameraConfig:
        return CameraConfig(
            selection_mode="first_available",
            device_id="",
            timeout_ms=self.camera_timeout.value(),
            pixel_format=str(self.pixel_format.currentData() or ""),
            resolution_mode=self._combo_value(self.resolution_mode),
            width=self.frame_width.value(),
            height=self.frame_height.value(),
            center_roi=self.center_roi.isChecked(),
            offset_x=self.offset_x.value(),
            offset_y=self.offset_y.value(),
            exposure_auto=self._combo_value(self.exposure_auto),
            exposure_us=self.exposure.value(),
            gain_auto=self._combo_value(self.gain_auto),
            gain_db=self.gain.value(),
            balance_white_auto=self._combo_value(self.balance_white_auto),
            balance_ratio_red=self.balance_red.value(),
            balance_ratio_green=self.balance_green.value(),
            balance_ratio_blue=self.balance_blue.value(),
            black_level_enabled=self.black_level_enabled.isChecked(),
            black_level=self.black_level.value(),
            gamma_enabled=self.gamma_enabled.isChecked(),
            gamma=self.gamma.value(),
            frame_rate_enabled=self.frame_rate_enabled.isChecked(),
            frame_rate_fps=self.frame_rate.value(),
            trigger_mode=self._combo_value(self.acquisition_mode),
            trigger_source="Software",
        ).normalized()

    def _camera_image_controls(self) -> list[QWidget]:
        """The controls whose effect a technician tunes by eye."""

        return [
            self.exposure_auto,
            self.exposure,
            self.gain_auto,
            self.gain,
            self.balance_white_auto,
            self.balance_red,
            self.balance_green,
            self.balance_blue,
            self.black_level_enabled,
            self.black_level,
            self.gamma_enabled,
            self.gamma,
        ]

    def _preview_settings_changed(self, *_args) -> None:
        if self.controller.camera_preview_active:
            self._preview_debounce.start()

    @staticmethod
    def _ratio_spin(value: float) -> QDoubleSpinBox:
        """A white-balance channel ratio. Zero means leave the channel alone."""

        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(0.0, 16.0)
        spin.setSingleStep(0.01)
        spin.setValue(max(0.0, float(value)))
        spin.setSpecialValueText("camera default")
        return spin

    # --- live preview -------------------------------------------------------

    def _toggle_live_preview(self) -> None:
        if self.controller.camera_preview_active:
            self.controller.stop_camera_preview(restore=True)
            return
        if not self.controller.start_camera_preview():
            self.live_preview_status.setText(
                "The camera is busy with an inspection or another capture. Try again in a moment."
            )
            return
        # Push what is on screen straight away, so the first frame already shows
        # the settings being tuned rather than the ones last saved.
        self._push_preview_settings()

    def _push_preview_settings(self) -> None:
        """Send the on-screen settings to the camera, for preview only."""

        if not self.controller.camera_preview_active:
            return
        self.controller.preview_camera_settings(self._camera_config_from_controls())

    def _camera_preview_frame(self, frame: object) -> None:
        if frame is None:
            return
        self.live_preview.set_pixmap_source(
            QPixmap.fromImage(bgr_array_to_qimage(frame))
        )

    def _camera_preview_state(self, running: bool, message: str) -> None:
        self.live_preview_button.setText(
            "STOP LIVE PREVIEW" if running else "START LIVE PREVIEW"
        )
        self.live_preview_button.setObjectName("DangerButton" if running else "")
        self.live_preview_button.style().unpolish(self.live_preview_button)
        self.live_preview_button.style().polish(self.live_preview_button)
        self.live_preview_status.setText(
            message
            + (
                "  Settings are being written to the camera and are not saved. "
                "Inspections are blocked while this runs."
                if running
                else ""
            )
        )
        self.live_preview_status.setStyleSheet(
            f"color: {AMBER}; font-weight: 700;" if running else ""
        )
        if not running:
            self.live_preview.set_pixmap_source(QPixmap())

    def _collect_config(self) -> AppConfig:
        tags = PlcTagMap(**{key: edit.text().strip() for key, edit in self.tag_edits.items()})
        return AppConfig(
            camera_backend=self._combo_value(self.camera_backend),
            plc_backend=self._combo_value(self.plc_mode),
            fullscreen=self.fullscreen.isChecked(),
            data_directory=self.controller.config.data_directory,
            camera=self._camera_config_from_controls(),
            ml=MlConfig(
                model_path=self.ml_model_path.text().strip(),
                manifest_path=self.ml_manifest_path.text().strip(),
                use_for_new_revisions=self.ml_use_new_revisions.isChecked(),
            ).normalized(),
            plc_address=self.plc_address.text().strip(),
            plc_poll_ms=self.plc_poll.value(),
            plc_heartbeat_ms=self.plc_heartbeat.value(),
            plc_recipe_selector=self._combo_value(self.plc_recipe_selector),
            plc_recipe_source=self._combo_value(self.plc_recipe_source),
            failure_retention_days=self.failure_retention_days.value(),
            failure_retention_max_gb=self.failure_retention_max_gb.value(),
            operator_name=self.operator.text().strip() or "Technician",
            validation_runs_required=self.validation_runs.value(),
            maintenance_passcode_salt=self.controller.config.maintenance_passcode_salt,
            maintenance_passcode_hash=self.controller.config.maintenance_passcode_hash,
            tags=tags,
        ).normalized()

    def _camera_configuration_patch(self, updated: AppConfig) -> AppConfig:
        return replace(
            self.controller.config,
            camera_backend=updated.camera_backend,
            camera=updated.camera,
            fullscreen=updated.fullscreen,
            operator_name=updated.operator_name,
        ).normalized()

    def _plc_configuration_patch(self, updated: AppConfig) -> AppConfig:
        return replace(
            self.controller.config,
            plc_backend=updated.plc_backend,
            plc_address=updated.plc_address,
            plc_poll_ms=updated.plc_poll_ms,
            plc_heartbeat_ms=updated.plc_heartbeat_ms,
            plc_recipe_selector=updated.plc_recipe_selector,
            plc_recipe_source=updated.plc_recipe_source,
            tags=updated.tags,
            fullscreen=updated.fullscreen,
            operator_name=updated.operator_name,
        ).normalized()

    def _ml_configuration_patch(self, updated: AppConfig) -> AppConfig:
        return replace(
            self.controller.config,
            ml=updated.ml,
            fullscreen=updated.fullscreen,
            operator_name=updated.operator_name,
        ).normalized()

    @staticmethod
    def _camera_changed(current: AppConfig, updated: AppConfig) -> bool:
        return (
            current.camera_backend != updated.camera_backend
            or asdict(current.camera) != asdict(updated.camera)
        )

    @staticmethod
    def _plc_changed(current: AppConfig, updated: AppConfig) -> bool:
        return (
            current.plc_backend != updated.plc_backend
            or current.plc_address != updated.plc_address
            or current.plc_poll_ms != updated.plc_poll_ms
            or current.plc_heartbeat_ms != updated.plc_heartbeat_ms
            or current.plc_recipe_selector != updated.plc_recipe_selector
            or current.plc_recipe_source != updated.plc_recipe_source
            or asdict(current.tags) != asdict(updated.tags)
        )

    @staticmethod
    def _ml_changed(
        current: AppConfig,
        updated: AppConfig,
        *,
        user_edited: bool,
    ) -> bool:
        return ml_configuration_requires_apply(
            current.ml,
            updated.ml,
            user_edited=user_edited,
        )

    def apply_camera_only(self) -> None:
        if self._save_in_progress:
            return
        requested = self._collect_config()
        updated = self._camera_configuration_patch(requested)
        self.set_camera_status("APPLYING SETTINGS AND CAPTURING TEST FRAME...", "warning")
        if not self.controller.apply_camera_settings(updated.camera, updated):
            self.set_camera_status(
                "CAMERA SETTINGS NOT APPLIED — ANOTHER CAMERA OPERATION IS ALREADY IN PROGRESS",
                "warning",
            )

    def apply_plc_only(self) -> None:
        if self._save_in_progress:
            return
        requested = self._collect_config()
        updated = self._plc_configuration_patch(requested)
        mode = "SIMULATION" if updated.plc_backend == "simulation" else "PYCOMM3"
        self.set_plc_status(f"APPLYING AND TESTING {mode} PLC SETTINGS...", "warning")
        if not self.controller.apply_plc_settings(updated):
            self.set_plc_status(
                "PLC SETTINGS NOT APPLIED — ANOTHER PLC CONFIGURATION IS ALREADY IN PROGRESS",
                "warning",
            )

    def save(self) -> None:
        if self._save_in_progress or self._camera_busy or self._plc_busy:
            QMessageBox.information(
                self,
                "Settings operation in progress",
                "Wait for the current camera or PLC operation to finish.",
            )
            return

        updated = self._collect_config()
        current = self.controller.config.normalized()
        steps: list[str] = []
        # PLC is applied first so a technician can immediately remove a failed
        # pycomm3 dependency by selecting Simulation, without waiting on camera work.
        if self._plc_changed(current, updated):
            steps.append("plc")
        if self._camera_changed(current, updated):
            steps.append("camera")
        if self._ml_changed(
            current,
            updated,
            user_edited=self._ml_settings_touched,
        ):
            steps.append("ml")

        if not steps:
            final = replace(
                current,
                fullscreen=updated.fullscreen,
                failure_retention_days=updated.failure_retention_days,
                failure_retention_max_gb=updated.failure_retention_max_gb,
                operator_name=updated.operator_name,
            ).normalized()
            self.controller.update_configuration(final)
            self._mark_save_complete()
            return

        self._pending_save_config = updated
        self._pending_save_steps = steps
        self._active_save_step = None
        self._save_in_progress = True
        self.save_state.setText("APPLYING SETTINGS...")
        self.save_state.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        self._refresh_action_buttons()
        self._run_next_save_step()

    def _run_next_save_step(self) -> None:
        if not self._save_in_progress or self._active_save_step is not None:
            return
        if self._camera_busy or self._plc_busy:
            return
        if self._pending_save_config is None:
            self._abort_save("SETTINGS PAYLOAD WAS LOST")
            return
        if not self._pending_save_steps:
            updated = self._pending_save_config
            # Camera/PLC workers persist their verified effective settings. Apply
            # only general fields here so a camera's aligned ROI is not overwritten
            # by the technician's unaligned request values.
            final = replace(
                self.controller.config,
                fullscreen=updated.fullscreen,
                failure_retention_days=updated.failure_retention_days,
                failure_retention_max_gb=updated.failure_retention_max_gb,
                operator_name=updated.operator_name,
            ).normalized()
            self.controller.update_configuration(final)
            self._mark_save_complete()
            return

        step = self._pending_save_steps.pop(0)
        self._active_save_step = step
        if step == "plc":
            updated = self._plc_configuration_patch(self._pending_save_config)
            self.set_plc_status("APPLYING AND TESTING PLC SETTINGS...", "warning")
            accepted = self.controller.apply_plc_settings(updated)
        elif step == "camera":
            updated = self._camera_configuration_patch(self._pending_save_config)
            self.set_camera_status("APPLYING SETTINGS AND CAPTURING TEST FRAME...", "warning")
            accepted = self.controller.apply_camera_settings(updated.camera, updated)
        else:
            updated = self._ml_configuration_patch(self._pending_save_config)
            try:
                info = self.controller.apply_ml_configuration(
                    model_path=updated.ml.model_path,
                    manifest_path=updated.ml.manifest_path,
                    use_for_new_revisions=updated.ml.use_for_new_revisions,
                )
            except Exception as exc:  # noqa: BLE001
                self._active_save_step = None
                self.set_ml_model_info({"ready": False, "issues": [str(exc)]})
                self._abort_save("ML MODEL FAILED VALIDATION")
                QMessageBox.critical(self, "ML model could not be loaded", str(exc))
                return
            self.set_ml_model_info(info)
            self._operation_step_succeeded("ml")
            QTimer.singleShot(0, self._run_next_save_step)
            return

        if not accepted:
            self._active_save_step = None
            self._abort_save(
                f"{step.upper()} SETTINGS NOT APPLIED — AN OPERATION FOR THAT SERVICE IS ALREADY IN PROGRESS"
            )

    def _operation_step_succeeded(self, step: str) -> None:
        if self._save_in_progress and self._active_save_step == step:
            if step == "ml":
                self._ml_settings_touched = False
            self._active_save_step = None

    def _abort_save(self, reason: str) -> None:
        if not self._save_in_progress:
            return
        self._save_in_progress = False
        self._pending_save_config = None
        self._pending_save_steps = []
        self._active_save_step = None
        self.save_state.setText(reason)
        self.save_state.setStyleSheet(f"color: {BAD}; font-weight: 700;")
        self._refresh_action_buttons()

    def _mark_save_complete(self) -> None:
        self._save_in_progress = False
        self._pending_save_config = None
        self._pending_save_steps = []
        self._active_save_step = None
        self._ml_settings_touched = False
        self.save_state.setText("ALL SETTINGS SAVED AND APPLIED")
        self.save_state.setStyleSheet(f"color: {GOOD}; font-weight: 700;")
        self._refresh_action_buttons()

    def _refresh_action_buttons(self) -> None:
        camera_blocked = (
            self._camera_busy or self._save_in_progress or self._station_transfer_busy
        )
        plc_blocked = self._plc_busy or self._save_in_progress or self._station_transfer_busy
        save_blocked = (
            self._camera_busy
            or self._plc_busy
            or self._save_in_progress
            or self._station_transfer_busy
        )
        self.scan_camera_button.setEnabled(not camera_blocked)
        self.apply_camera_button.setEnabled(not camera_blocked)
        self.apply_plc_button.setEnabled(not plc_blocked)
        self.ml_apply_button.setEnabled(not self._save_in_progress)
        self.ml_browse_model.setEnabled(not self._save_in_progress)
        self.ml_browse_manifest.setEnabled(not self._save_in_progress)
        self.ml_model_path.setEnabled(not self._save_in_progress)
        self.ml_manifest_path.setEnabled(not self._save_in_progress)
        self.ml_use_new_revisions.setEnabled(not self._save_in_progress)
        self.save_button.setEnabled(not save_blocked)
        self.export_backup_button.setEnabled(not save_blocked)
        self.import_backup_button.setEnabled(not save_blocked)
        simulation_active = self.controller.plc_simulation_active
        self.simulate_plc_trigger_button.setEnabled(
            not camera_blocked and not plc_blocked and simulation_active
        )
