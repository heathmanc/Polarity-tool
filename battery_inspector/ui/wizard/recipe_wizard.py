from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.models import (
    InspectionDisposition,
    InspectionResult,
    Marking,
    NormalizedRect,
    Recipe,
    ReferenceCapture,
    TerminalFinish,
    TerminalRole,
)
from battery_inspector.recipe_draft import RecipeDraft
from battery_inspector.roi_geometry import (
    CIRCLE_ROI_SHAPE,
    TAUGHT_CIRCLE_CROP_CONTRACT,
    coerce_circle_rect,
    normalize_roi_shape,
)
from battery_inspector.ui.image_widgets import (
    ImageOverlayWidget,
    OverlaySpec,
    PolygonOverlaySpec,
    RoiEditor,
)
from battery_inspector.ui.palette import (
    AMBER,
    AMBER_BG,
    BAD,
    BAD_BG,
    BLUE,
    BORDER,
    GOOD,
    GOOD_BG,
    ROI_BATTERY,
    ROI_MARKING,
    ROLE_NEGATIVE,
    ROLE_POSITIVE,
    SURFACE_STRONG,
    TEXT_MUTED,
)
from battery_inspector.ui.widgets import PanelFrame, StepIndicator

if TYPE_CHECKING:
    from battery_inspector.controller import AppController


RecipeWizardData = RecipeDraft


def _reference_path(data: RecipeWizardData) -> Path | None:
    if data.reference_image is None or not data.reference_image.path:
        return None
    path = Path(data.reference_image.path)
    return path if path.exists() else None


class WizardPage(QWidget):
    def prepare(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def can_continue(self) -> tuple[bool, str]:
        return True, ""


class ReferenceCapturePage(WizardPage):
    CAPTURE_LABEL = "CAPTURE NEW REFERENCE"
    RETAKE_LABEL = "RETAKE"

    """Capture, review, retake, and explicitly accept a reference frame."""

    def __init__(
        self,
        data: RecipeWizardData,
        controller: AppController,
        source_recipe: Recipe | None,
    ) -> None:
        super().__init__()
        self.data = data
        self.controller = controller
        self.source_recipe = source_recipe
        self.pending_reference: ReferenceCapture | None = None
        self._busy = False

        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(12)

        image_panel = PanelFrame()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("Step 1: Capture the recipe reference")
        title.setObjectName("PanelTitle")
        image_layout.addWidget(title)
        instruction = QLabel(
            "Place a known-good battery in the stopped inspection position. The accepted image "
            "becomes immutable evidence for this recipe revision."
        )
        instruction.setWordWrap(True)
        instruction.setProperty("muted", True)
        image_layout.addWidget(instruction)
        self.image = ImageOverlayWidget()
        self.image.setMinimumSize(640, 420)
        image_layout.addWidget(self.image, 1)
        self.image_status = QLabel("CAPTURE REQUIRED")
        self.image_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_layout.addWidget(self.image_status)
        root.addWidget(image_panel, 4)

        side = PanelFrame()
        side.setMinimumWidth(360)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(16, 16, 16, 16)
        side_title = QLabel("REFERENCE IMAGE")
        side_title.setObjectName("PanelTitle")
        side_layout.addWidget(side_title)
        self.details = QLabel("Reference: NONE")
        self.details.setWordWrap(True)
        self.details.setStyleSheet("font-family: Consolas, monospace; font-size: 13px;")
        side_layout.addWidget(self.details)

        # One button, because there is one action: acquire a fresh frame. It
        # was two -- CAPTURE NEW REFERENCE and RETAKE -- wired to the same slot
        # and both visible once a capture existed, which reads as a choice that
        # is not there. The label says which of the two situations you are in.
        self.capture_button = QPushButton(self.CAPTURE_LABEL)
        self.capture_button.setObjectName("PrimaryButton")
        self.capture_button.clicked.connect(self.capture_new)
        side_layout.addWidget(self.capture_button)

        self.use_button = QPushButton("USE THIS IMAGE")
        self.use_button.setObjectName("PrimaryButton")
        self.use_button.clicked.connect(self.use_pending)
        self.use_button.setVisible(False)
        side_layout.addWidget(self.use_button)

        self.keep_button = QPushButton("KEEP EXISTING REFERENCE")
        self.keep_button.clicked.connect(self.keep_existing)
        side_layout.addWidget(self.keep_button)

        self.message = QLabel()
        self.message.setWordWrap(True)
        side_layout.addWidget(self.message)
        side_layout.addStretch(1)
        reminder = QLabel(
            "The wizard will not continue until USE THIS IMAGE or KEEP EXISTING REFERENCE is selected."
        )
        reminder.setWordWrap(True)
        reminder.setProperty("muted", True)
        side_layout.addWidget(reminder)
        root.addWidget(side, 2)

        controller.reference_capture_completed.connect(self._capture_completed)
        controller.reference_capture_failed.connect(self._capture_failed)
        controller.reference_capture_busy.connect(self._capture_busy)
        self.prepare()

    def prepare(self) -> None:
        reference = self.pending_reference or self.data.reference_image
        path = Path(reference.path) if reference and reference.path else None
        self.image.set_image(path if path and path.exists() else None)
        if reference is not None:
            self._show_reference_details(reference)
        else:
            self.details.setText("Reference: NONE")

        existing_ok = bool(
            self.source_recipe
            and self.source_recipe.reference_image
            and Path(self.source_recipe.reference_image.path).exists()
        )
        self.keep_button.setVisible(self.source_recipe is not None)
        self.keep_button.setEnabled(existing_ok and not self._busy)
        self.capture_button.setEnabled(not self._busy)
        self.capture_button.setText(
            self.RETAKE_LABEL if self.pending_reference is not None else self.CAPTURE_LABEL
        )
        self.use_button.setVisible(self.pending_reference is not None)
        self.use_button.setEnabled(
            not self._busy
            and (self.pending_reference is None or self.pending_reference.acceptable_for_recipe)
        )

        if self._busy:
            self._set_status("CAPTURING FRESH FRAME…", BLUE)
            self._set_message("The camera is acquiring a new frame. The previous image will not be reused.", BLUE)
        elif self.data.reference_accepted and self.data.reference_image is not None:
            self._set_status("REFERENCE ACCEPTED", GOOD)
            self._set_message(
                "This image will be copied into the immutable recipe revision when saved.",
                GOOD,
            )
        elif self.pending_reference is not None and not self.pending_reference.acceptable_for_recipe:
            self._set_status("RETAKE REQUIRED — IMAGE QUALITY POOR", BAD)
            self._set_message(
                "The exposure/focus quality gate failed. Correct the camera or lighting and select RETAKE.",
                BAD,
            )
        elif self.pending_reference is not None:
            self._set_status("NEW CAPTURE READY — REVIEW IT", AMBER)
            self._set_message(
                "Check framing, exposure, focus, and battery condition. Select USE THIS IMAGE or RETAKE.",
                AMBER,
            )
        elif existing_ok:
            self._set_status("CHOOSE KEEP EXISTING OR CAPTURE NEW", AMBER)
            self._set_message(
                "A new capture is recommended whenever the fixture, camera, or taught geometry changed.",
                AMBER,
            )
        else:
            self._set_status("CAPTURE REQUIRED", BAD)
            self._set_message(
                "No usable reference exists for this revision. Capture a fresh camera image to continue.",
                BAD,
            )

    def _set_status(self, text: str, color: str) -> None:
        self.image_status.setText(text)
        self.image_status.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: 800;")

    def _set_message(self, text: str, color: str) -> None:
        self.message.setText(text)
        self.message.setStyleSheet(
            f"color: {color}; padding: 10px; background: {SURFACE_STRONG}; border: 1px solid {color};"
        )

    def capture_new(self) -> None:
        self.data.clear_reference_acceptance()
        if not self.controller.capture_recipe_reference():
            QMessageBox.information(
                self,
                "Camera is occupied",
                "Wait for the current camera operation or inspection to finish, then capture again.",
            )

    def _capture_completed(self, payload: object) -> None:
        if not isinstance(payload, ReferenceCapture):
            self._capture_failed("The camera returned an invalid reference-capture record")
            return
        self.pending_reference = payload
        self.data.clear_reference_acceptance()
        self.prepare()

    def _capture_failed(self, message: str) -> None:
        self._busy = False
        self._set_status("REFERENCE CAPTURE FAILED", BAD)
        self._set_message(message, BAD)
        self.capture_button.setEnabled(True)

    def _capture_busy(self, busy: bool) -> None:
        self._busy = busy
        self.prepare()

    def use_pending(self) -> None:
        if self.pending_reference is None:
            return
        if not self.pending_reference.acceptable_for_recipe:
            QMessageBox.warning(
                self,
                "Reference image quality is poor",
                "This frame failed the exposure/focus quality gate and cannot be used as a recipe "
                "reference. Correct the camera or lighting and select RETAKE.",
            )
            self._set_status("RETAKE REQUIRED — IMAGE QUALITY POOR", BAD)
            return
        self.data.set_reference(self.pending_reference, changed=True)
        self.prepare()

    def keep_existing(self) -> None:
        reference = self.source_recipe.reference_image if self.source_recipe else None
        if reference is None or not Path(reference.path).exists():
            QMessageBox.warning(
                self,
                "Reference unavailable",
                "The previous revision's reference file is unavailable. Capture a new image.",
            )
            return
        self.pending_reference = None
        self.data.set_reference(reference, changed=False)
        self.prepare()

    def _show_reference_details(self, reference: ReferenceCapture) -> None:
        quality = reference.quality
        self.details.setText(
            f"Captured: {reference.captured_at_utc or 'unknown'}\n"
            f"Resolution: {reference.width_px} x {reference.height_px}\n"
            f"Channels: {reference.channels}\n"
            f"Frame: {reference.camera_frame_id or reference.frame_id or reference.frame_sequence or 'n/a'}\n"
            f"Source: {reference.source}\n"
            f"Quality: {quality.get('status', 'UNKNOWN')}\n"
            f"Mean level: {quality.get('mean_level', 'n/a')}\n"
            f"Sharpness: {quality.get('sharpness', 'n/a')}"
        )

    def detach(self) -> None:
        for signal, slot in (
            (self.controller.reference_capture_completed, self._capture_completed),
            (self.controller.reference_capture_failed, self._capture_failed),
            (self.controller.reference_capture_busy, self._capture_busy),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def can_continue(self) -> tuple[bool, str]:
        if not self.data.reference_accepted or self.data.reference_image is None:
            return False, "Capture and accept a reference image, or explicitly keep the existing reference."
        if not Path(self.data.reference_image.path).exists():
            return False, "The accepted reference file is no longer available. Capture it again."
        if not self.data.reference_image.acceptable_for_recipe:
            return (
                False,
                "The reference image failed the exposure/focus quality gate. Correct the image and retake it.",
            )
        return True, ""


class IdentifyPage(WizardPage):
    def __init__(self, data: RecipeWizardData, *, number_locked: bool = False) -> None:
        super().__init__()
        self.data = data
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(12)

        form_panel = PanelFrame()
        form_layout = QVBoxLayout(form_panel)
        form_layout.setContentsMargins(20, 18, 20, 18)
        title = QLabel("Step 2: Identify the battery recipe")
        title.setObjectName("PanelTitle")
        form_layout.addWidget(title)
        help_text = QLabel(
            "Enter the production-facing identifiers. Technicians never edit JSON or model settings."
        )
        help_text.setWordWrap(True)
        help_text.setProperty("muted", True)
        form_layout.addWidget(help_text)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.recipe_number = QSpinBox()
        self.recipe_number.setRange(1, 2_147_483_647)
        self.recipe_number.setValue(max(1, data.recipe_number))
        self.recipe_number.setEnabled(not number_locked)
        self.name = QLineEdit(data.name)
        self.part_number = QLineEdit(data.part_number)
        self.description = QTextEdit(data.description)
        self.description.setFixedHeight(96)
        self.description.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.description.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        form.addRow("Recipe number", self.recipe_number)
        form.addRow("Recipe name", self.name)
        form.addRow("Part number", self.part_number)
        form.addRow("Description", self.description)
        form_layout.addLayout(form)
        note = QLabel(
            "Recipe number and name must both be unique. The PLC can select this recipe "
            "by its STRING name or by its integer number, as configured on the PLC tab."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {AMBER}; padding: 10px; background: {AMBER_BG}; border: 1px solid {AMBER};")
        form_layout.addWidget(note)
        form_layout.addStretch(1)
        root.addWidget(form_panel, 2)

        image_panel = PanelFrame()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(12, 12, 12, 12)
        image_title = QLabel("ACCEPTED REFERENCE")
        image_title.setObjectName("PanelTitle")
        image_layout.addWidget(image_title)
        self.image = ImageOverlayWidget()
        image_layout.addWidget(self.image, 1)
        root.addWidget(image_panel, 3)

    def prepare(self) -> None:
        self.image.set_image(_reference_path(self.data))

    def commit(self) -> None:
        self.data.recipe_number = self.recipe_number.value()
        self.data.name = self.name.text().strip().upper().replace(" ", "_")
        self.data.part_number = self.part_number.text().strip()
        self.data.description = self.description.toPlainText().strip()

    def can_continue(self) -> tuple[bool, str]:
        self.commit()
        if not self.data.name:
            return False, "Recipe name is required."
        if not self.data.part_number:
            return False, "Part number is required."
        return True, ""


class DefineBatteryPage(WizardPage):
    def __init__(self, data: RecipeWizardData) -> None:
        super().__init__()
        self.data = data
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(12)

        image_panel = PanelFrame()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("Step 3: Define the battery outline")
        title.setObjectName("PanelTitle")
        image_layout.addWidget(title)
        instruction = QLabel(
            "Drag inside the box to move it. Drag outside the current box to redraw it. "
            "The box must surround the battery case—not loose hardware or the table."
        )
        instruction.setWordWrap(True)
        instruction.setProperty("muted", True)
        image_layout.addWidget(instruction)
        self.editor = RoiEditor()
        self.editor.roi_changed.connect(self._roi_changed)
        image_layout.addWidget(self.editor, 1)
        root.addWidget(image_panel, 4)

        side = PanelFrame()
        side.setMinimumWidth(285)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(16, 16, 16, 16)
        side_title = QLabel("BATTERY OUTLINE")
        side_title.setObjectName("PanelTitle")
        side_layout.addWidget(side_title)
        self.coordinates = QLabel()
        self.coordinates.setStyleSheet("font-family: Consolas, monospace; font-size: 14px;")
        side_layout.addWidget(self.coordinates)
        legend = QLabel("SOLID BLUE = taught battery boundary")
        legend.setStyleSheet(f"color: {ROI_BATTERY}; font-weight: 700;")
        legend.setWordWrap(True)
        side_layout.addWidget(legend)
        orientation_label = QLabel("ORIENTATION REFERENCE")
        orientation_label.setObjectName("SectionTitle")
        side_layout.addWidget(orientation_label)
        self.orientation = QComboBox()
        self.orientation.addItem(
            "Case outline + non-terminal features",
            "terminal_layout_and_case_outline",
        )
        self.orientation.addItem("Unique case notch / label feature", "case_feature")
        self.orientation.addItem("Station / conveyor direction", "station_direction")
        self.orientation.currentIndexChanged.connect(self._orientation_changed)
        side_layout.addWidget(self.orientation)
        orientation_note = QLabel(
            "The reference must distinguish a 180° rotation using the case, notch, vent, or label. "
            "Terminal stamps and the red ring are deliberately excluded from orientation."
        )
        orientation_note.setWordWrap(True)
        orientation_note.setStyleSheet(f"color: {AMBER};")
        side_layout.addWidget(orientation_note)
        smaller = QPushButton("MAKE 5% SMALLER")
        larger = QPushButton("MAKE 5% LARGER")
        reset = QPushButton("RESET PROPOSED OUTLINE")
        smaller.clicked.connect(lambda: self.editor.nudge_size("battery", 0.95))
        larger.clicked.connect(lambda: self.editor.nudge_size("battery", 1.05))
        reset.clicked.connect(lambda: self.editor.set_roi("battery", NormalizedRect(0.24, 0.015, 0.59, 0.92)))
        side_layout.addWidget(smaller)
        side_layout.addWidget(larger)
        side_layout.addWidget(reset)
        side_layout.addStretch(1)
        root.addWidget(side, 1)

    def prepare(self) -> None:
        self.editor.set_image(_reference_path(self.data))
        self.editor.set_editable_rois(
            [OverlaySpec("battery", self.data.battery_roi, "BATTERY OUTLINE", ROI_BATTERY, line_width=4)]
        )
        current = self.orientation.findData(self.data.orientation_reference)
        self.orientation.setCurrentIndex(max(0, current))
        self._update_coordinates(self.data.battery_roi)

    def _orientation_changed(self) -> None:
        self.data.orientation_reference = str(self.orientation.currentData())

    def _roi_changed(self, key: str, rect: NormalizedRect) -> None:
        if key == "battery":
            self.data.battery_roi = rect
            self._update_coordinates(rect)

    def _update_coordinates(self, rect: NormalizedRect) -> None:
        self.coordinates.setText(
            f"Image width covered:  {rect.width * 100:0.1f}%\n"
            f"Image height covered: {rect.height * 100:0.1f}%\n"
            f"Left clearance:       {rect.x * 100:0.1f}%\n"
            f"Top clearance:        {rect.y * 100:0.1f}%"
        )

    def can_continue(self) -> tuple[bool, str]:
        if self.data.battery_roi.width < 0.15 or self.data.battery_roi.height < 0.15:
            return False, "The battery outline is too small. Redraw the full battery boundary."
        return True, ""


class DefineTerminalsPage(WizardPage):
    def __init__(self, data: RecipeWizardData) -> None:
        super().__init__()
        self.data = data
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(12)
        image_panel = PanelFrame()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("Step 4: Teach the physical terminals")
        title.setObjectName("PanelTitle")
        image_layout.addWidget(title)
        instruction = QLabel(
            "Select NEGATIVE or POSITIVE, then drag a solid box around the complete terminal assembly. "
            "The enlarged crop at right is exactly what the inspection will search."
        )
        instruction.setWordWrap(True)
        instruction.setProperty("muted", True)
        image_layout.addWidget(instruction)
        self.editor = RoiEditor()
        self.editor.roi_changed.connect(self._roi_changed)
        self.editor.selection_changed.connect(self._selection_changed)
        image_layout.addWidget(self.editor, 1)
        root.addWidget(image_panel, 4)

        side = PanelFrame()
        side.setMinimumWidth(360)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(14, 14, 14, 14)
        self.active_title = QLabel("NEGATIVE TERMINAL")
        self.active_title.setObjectName("PanelTitle")
        side_layout.addWidget(self.active_title)
        selector = QHBoxLayout()
        self.negative_button = QPushButton("1  NEGATIVE")
        self.positive_button = QPushButton("2  POSITIVE")
        self.negative_button.setCheckable(True)
        self.positive_button.setCheckable(True)
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.negative_button)
        group.addButton(self.positive_button)
        self.negative_button.clicked.connect(lambda: self.editor.set_active_key("negative"))
        self.positive_button.clicked.connect(lambda: self.editor.set_active_key("positive"))
        selector.addWidget(self.negative_button)
        selector.addWidget(self.positive_button)
        side_layout.addLayout(selector)
        crop_label = QLabel("TERMINAL SEARCH CROP")
        crop_label.setObjectName("SectionTitle")
        side_layout.addWidget(crop_label)
        self.crop = RoiEditor()
        self.crop.setMinimumSize(280, 220)
        self.crop.roi_changed.connect(self._marking_roi_changed)
        side_layout.addWidget(self.crop, 1)
        self.marking_legend = QLabel(
            "SOLID = terminal search area\n"
            "DASHED CIRCLE = metal terminal face used for polarity classification"
        )
        self.marking_legend.setWordWrap(True)
        self.marking_legend.setStyleSheet(f"color: {TEXT_MUTED};")
        side_layout.addWidget(self.marking_legend)
        terminal_size_label = QLabel("TERMINAL SEARCH AREA")
        terminal_size_label.setObjectName("SectionTitle")
        side_layout.addWidget(terminal_size_label)
        size_row = QHBoxLayout()
        smaller = QPushButton("TERMINAL −")
        larger = QPushButton("TERMINAL +")
        smaller.clicked.connect(lambda: self._resize_active(0.94))
        larger.clicked.connect(lambda: self._resize_active(1.06))
        size_row.addWidget(smaller)
        size_row.addWidget(larger)
        side_layout.addLayout(size_row)
        self.marking_size_label = QLabel("MARKING CIRCLE")
        self.marking_size_label.setObjectName("SectionTitle")
        side_layout.addWidget(self.marking_size_label)
        marking_size_row = QHBoxLayout()
        self.marking_smaller = QPushButton("CIRCLE −")
        self.marking_larger = QPushButton("CIRCLE +")
        self.marking_redraw = QPushButton("REDRAW CIRCLE")
        self.marking_smaller.clicked.connect(lambda: self.crop.nudge_size("marking", 0.92))
        self.marking_larger.clicked.connect(lambda: self.crop.nudge_size("marking", 1.08))
        self.marking_redraw.clicked.connect(lambda: self.crop.begin_redraw("marking"))
        marking_size_row.addWidget(self.marking_smaller)
        marking_size_row.addWidget(self.marking_larger)
        side_layout.addLayout(marking_size_row)
        side_layout.addWidget(self.marking_redraw)
        root.addWidget(side, 2)

    def prepare(self) -> None:
        self.editor.set_image(_reference_path(self.data))
        self.editor.set_static_overlays(
            [OverlaySpec("battery-static", self.data.battery_roi, "BATTERY", ROI_BATTERY, dashed=True, line_width=2)]
        )
        self.editor.set_editable_rois(
            [
                OverlaySpec("negative", self.data.terminal_rois["negative"], "1  NEGATIVE", ROLE_NEGATIVE, line_width=4),
                OverlaySpec("positive", self.data.terminal_rois["positive"], "2  POSITIVE", ROLE_POSITIVE, line_width=4),
            ]
        )
        self.editor.set_active_key(self.editor.active_key() or "negative")
        self._refresh_crop(self.editor.active_key() or "negative")

    def _resize_active(self, scale: float) -> None:
        self.editor.nudge_size(self.editor.active_key() or "negative", scale)

    def _roi_changed(self, key: str, rect: NormalizedRect) -> None:
        self.data.terminal_rois[key] = rect
        self._refresh_crop(key)

    def _selection_changed(self, key: str) -> None:
        self.negative_button.setChecked(key == "negative")
        self.positive_button.setChecked(key == "positive")
        color = ROLE_NEGATIVE if key == "negative" else ROLE_POSITIVE
        self.active_title.setText(f"{key.upper()} TERMINAL")
        self.active_title.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: 700;")
        self._refresh_crop(key)

    def _refresh_crop(self, key: str) -> None:
        terminal_pixmap = self.editor.crop_pixmap(self.data.terminal_rois[key])
        self.crop.set_pixmap_source(terminal_pixmap)
        shape = normalize_roi_shape(
            self.data.marking_roi_shapes.get(key, CIRCLE_ROI_SHAPE)
        )
        if shape == CIRCLE_ROI_SHAPE and not terminal_pixmap.isNull():
            self.data.marking_rois[key] = coerce_circle_rect(
                self.data.marking_rois[key],
                terminal_pixmap.width(),
                terminal_pixmap.height(),
            )
        self.data.marking_roi_shapes[key] = shape
        if shape == CIRCLE_ROI_SHAPE:
            label = "MARKING CIRCLE"
            self.marking_legend.setText(
                "SOLID = terminal search area\n"
                "DASHED CIRCLE = exact metal-face region used for polarity ML"
            )
            self.marking_size_label.setText("MARKING CIRCLE")
            self.marking_smaller.setText("CIRCLE −")
            self.marking_larger.setText("CIRCLE +")
            self.marking_redraw.setText("REDRAW CIRCLE")
        else:
            label = "LEGACY MARKING ROI"
            self.marking_legend.setText(
                "SOLID = terminal search area\n"
                "DASHED RECTANGLE = legacy exact model input. Install a circle-contract model to upgrade this revision."
            )
            self.marking_size_label.setText("LEGACY MARKING ROI")
            self.marking_smaller.setText("ROI −")
            self.marking_larger.setText("ROI +")
            self.marking_redraw.setText("REDRAW ROI")
        self.crop.set_editable_rois(
            [
                OverlaySpec(
                    "marking",
                    self.data.marking_rois[key],
                    label,
                    ROI_MARKING,
                    dashed=True,
                    line_width=3,
                    shape=shape,
                )
            ]
        )

    def _marking_roi_changed(self, key: str, rect: NormalizedRect) -> None:
        if key != "marking":
            return
        active = self.editor.active_key() or "negative"
        self.data.marking_rois[active] = rect
        self.data.marking_roi_shapes[active] = normalize_roi_shape(
            self.data.marking_roi_shapes.get(active, CIRCLE_ROI_SHAPE)
        )

    def can_continue(self) -> tuple[bool, str]:
        for key, rect in self.data.terminal_rois.items():
            if rect.width < 0.025 or rect.height < 0.025:
                return False, f"The {key} terminal ROI is too small."
        return True, ""


class PolarityPage(WizardPage):
    def __init__(self, data: RecipeWizardData, controller: AppController) -> None:
        super().__init__()
        self.data = data
        self.controller = controller
        self.source = QPixmap()
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)
        title = QLabel("Step 5: Define polarity and ring requirements")
        title.setObjectName("PanelTitle")
        root.addWidget(title)
        instruction = QLabel(
            "For each physical terminal, choose both the required top marking and visible metal finish. "
            "The finish check is independent of polarity classification."
        )
        instruction.setWordWrap(True)
        instruction.setProperty("muted", True)
        root.addWidget(instruction)
        self.engine = QLabel()
        self.engine.setWordWrap(True)
        self.engine.setStyleSheet(
            f"padding: 8px; background: {SURFACE_STRONG}; border: 1px solid {BORDER};"
        )
        root.addWidget(self.engine)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        root.addLayout(cards, 1)
        self.controls: dict[str, dict[str, object]] = {}
        for key, role, color in (
            ("negative", TerminalRole.NEGATIVE, ROLE_NEGATIVE),
            ("positive", TerminalRole.POSITIVE, ROLE_POSITIVE),
        ):
            card = PanelFrame()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            header = QLabel(f"PHYSICAL {role.display} TERMINAL")
            header.setStyleSheet(f"color: {color}; font-size: 17px; font-weight: 800;")
            card_layout.addWidget(header)
            view = ImageOverlayWidget()
            view.setMinimumSize(300, 250)
            card_layout.addWidget(view, 1)
            form = QFormLayout()
            marking = QComboBox()
            for item in (Marking.PLUS, Marking.MINUS, Marking.BLANK):
                marking.addItem(item.display, item)
            finish = QComboBox()
            finish.addItem("SELECT FINISH", TerminalFinish.UNSPECIFIED)
            for item in (TerminalFinish.SILVER, TerminalFinish.BRASS):
                finish.addItem(item.display, item)
            ring = QCheckBox("Required around this terminal")
            form.addRow("Expected top marking", marking)
            form.addRow("Expected terminal finish", finish)
            form.addRow("Red ring", ring)
            card_layout.addLayout(form)
            confirmation = QLabel()
            confirmation.setWordWrap(True)
            confirmation.setStyleSheet(f"padding: 9px; background: {SURFACE_STRONG}; border: 1px solid {BORDER};")
            card_layout.addWidget(confirmation)
            marking.currentIndexChanged.connect(lambda _i, k=key: self._update_control(k))
            finish.currentIndexChanged.connect(lambda _i, k=key: self._update_control(k))
            ring.toggled.connect(lambda _v, k=key: self._update_control(k))
            self.controls[key] = {
                "view": view,
                "marking": marking,
                "finish": finish,
                "ring": ring,
                "confirmation": confirmation,
            }
            cards.addWidget(card)

    def _crop_for(self, key: str) -> QPixmap:
        if self.source.isNull():
            return QPixmap()
        rect = self.data.terminal_rois[key].clamped()
        return self.source.copy(
            int(rect.x * self.source.width()),
            int(rect.y * self.source.height()),
            max(1, int(rect.width * self.source.width())),
            max(1, int(rect.height * self.source.height())),
        )

    def prepare(self) -> None:
        settings = self.data.classifier_settings.normalized()
        if settings.method == "onnx_ml":
            info = self.controller.ml_model_info(require_runtime=False)
            self.engine.setText(
                "CLASSIFICATION ENGINE: ML / ONNX — "
                f"{settings.ml_model_id or info.get('model_id', 'MODEL NOT BOUND')} "
                f"{settings.ml_model_version or info.get('model_version', '')}\n"
                "The model receives only the isolated metal terminal-top crop. "
                "The red ring and molded case polarity symbols are inspected separately."
            )
        else:
            self.engine.setText(
                "CLASSIFICATION ENGINE: LEGACY REFERENCE / GEOMETRY HYBRID\n"
                "Install and enable an ONNX polarity model in Settings → VISION / ML "
                "to use ML on new or edited recipe revisions."
            )
        path = _reference_path(self.data)
        self.source = QPixmap(str(path)) if path else QPixmap()
        for key, controls in self.controls.items():
            marking = controls["marking"]
            finish = controls["finish"]
            ring = controls["ring"]
            view = controls["view"]
            assert isinstance(marking, QComboBox)
            assert isinstance(finish, QComboBox)
            assert isinstance(ring, QCheckBox)
            assert isinstance(view, ImageOverlayWidget)
            # Load every control before any of them is allowed to write back.
            # These widgets are connected to _update_control, which copies the
            # whole card into the draft. Without blocking, setting the marking
            # fired that handler while the finish and ring were still at their
            # construction defaults, and the handler overwrote the saved values
            # in the draft with those defaults -- so the very next line read an
            # UNSPECIFIED finish it had just erased. It only bit a terminal
            # whose marking was not already the combo's first item, which is
            # why an edited recipe came back with the negative terminal's
            # finish cleared and its red-ring requirement silently switched off
            # while the positive terminal looked fine.
            marking.blockSignals(True)
            finish.blockSignals(True)
            ring.blockSignals(True)
            try:
                index = marking.findData(self.data.expected_markings[key])
                marking.setCurrentIndex(max(0, index))
                finish_index = finish.findData(self.data.expected_finishes[key])
                finish.setCurrentIndex(max(0, finish_index))
                ring.setChecked(self.data.red_ring_required[key])
            finally:
                marking.blockSignals(False)
                finish.blockSignals(False)
                ring.blockSignals(False)
            view.set_pixmap_source(self._crop_for(key))
            view.set_overlays(
                [
                    OverlaySpec(
                        "marking",
                        self.data.marking_rois[key],
                        "MARKING CIRCLE",
                        ROI_MARKING,
                        dashed=True,
                        line_width=3,
                        shape=self.data.marking_roi_shapes.get(key, CIRCLE_ROI_SHAPE),
                    )
                ]
            )
            self._update_control(key)

    def _update_control(self, key: str) -> None:
        controls = self.controls[key]
        marking = controls["marking"]
        finish = controls["finish"]
        ring = controls["ring"]
        confirmation = controls["confirmation"]
        assert isinstance(marking, QComboBox)
        assert isinstance(finish, QComboBox)
        assert isinstance(ring, QCheckBox)
        assert isinstance(confirmation, QLabel)
        selected = marking.currentData()
        self.data.expected_markings[key] = selected if isinstance(selected, Marking) else Marking(str(selected))
        selected_finish = finish.currentData()
        self.data.expected_finishes[key] = (
            selected_finish
            if isinstance(selected_finish, TerminalFinish)
            else TerminalFinish(str(selected_finish))
        )
        self.data.red_ring_required[key] = ring.isChecked()
        confirmation.setText(
            f"Expected: {self.data.expected_markings[key].display}\n"
            f"Finish: {self.data.expected_finishes[key].display}\n"
            f"Red ring: {'YES' if self.data.red_ring_required[key] else 'NO'}"
        )

    def can_continue(self) -> tuple[bool, str]:
        missing = [
            key.upper()
            for key in ("negative", "positive")
            if self.data.expected_finishes.get(key, TerminalFinish.UNSPECIFIED)
            == TerminalFinish.UNSPECIFIED
        ]
        if missing:
            return False, "Select SILVER or BRASS for the " + " and ".join(missing) + " terminal."
        return True, ""


class ReadinessPage(WizardPage):
    """Run real, fresh-frame recipe validation samples."""

    def __init__(
        self,
        data: RecipeWizardData,
        controller: AppController,
        source_recipe: Recipe | None,
    ) -> None:
        super().__init__()
        self.data = data
        self.controller = controller
        self.source_recipe = source_recipe
        self._busy = False
        self._request_active = False
        self._last_result: InspectionResult | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(12)

        image_panel = PanelFrame()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("Step 6: Validate with known-good batteries")
        title.setObjectName("PanelTitle")
        image_layout.addWidget(title)
        instruction = QLabel(
            "Each validation run acquires a fresh camera frame and uses the exact production "
            "registration, terminal crops, silver/brass finish check, marking classifier, red-ring check, and evidence writer. "
            "Move or rotate the known-good battery between successful runs."
        )
        instruction.setWordWrap(True)
        instruction.setProperty("muted", True)
        image_layout.addWidget(instruction)
        self.image = ImageOverlayWidget()
        self.image.setMinimumSize(640, 420)
        image_layout.addWidget(self.image, 1)
        self.image_caption = QLabel("NO VALIDATION SAMPLE CAPTURED")
        self.image_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_caption.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 15px; font-weight: 800;"
        )
        image_layout.addWidget(self.image_caption)
        root.addWidget(image_panel, 4)

        side = PanelFrame()
        side.setMinimumWidth(430)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(16, 16, 16, 16)
        side_title = QLabel("REAL VALIDATION")
        side_title.setObjectName("PanelTitle")
        side_layout.addWidget(side_title)

        self.progress_text = QLabel()
        self.progress_text.setStyleSheet("font-size: 18px; font-weight: 800;")
        side_layout.addWidget(self.progress_text)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        side_layout.addWidget(self.progress)

        self.gates = QLabel()
        self.gates.setWordWrap(True)
        self.gates.setStyleSheet(
            f"padding: 10px; background: {SURFACE_STRONG}; border: 1px solid {BORDER};"
        )
        side_layout.addWidget(self.gates)

        self.result = QLabel("Waiting for validation sample")
        self.result.setWordWrap(True)
        self.result.setStyleSheet(
            f"padding: 12px; background: {SURFACE_STRONG}; border: 1px solid {BORDER}; "
            "font-size: 16px; font-weight: 700;"
        )
        side_layout.addWidget(self.result)

        self.metrics = QLabel("Locator metrics: —")
        self.metrics.setWordWrap(True)
        self.metrics.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        side_layout.addWidget(self.metrics)

        self.terminals = QLabel("Terminal results: —")
        self.terminals.setWordWrap(True)
        side_layout.addWidget(self.terminals)

        self.different_part = QCheckBox("A different battery is loaded")
        self.different_part.setToolTip(
            "Tick this when the battery in the fixture is a different physical part "
            "from the last counted sample. It is recorded with the sample."
        )
        side_layout.addWidget(self.different_part)

        self.run_button = QPushButton("RUN FRESH VALIDATION SAMPLE")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self.run_validation)
        side_layout.addWidget(self.run_button)

        reminder = QLabel(
            "A PASS counts when the sample is independent of the ones already counted: "
            "a different battery, confirmed above, or the same battery moved. On a fixed "
            "stop, use a different battery. Uncounted samples stay in the audit evidence."
        )
        reminder.setWordWrap(True)
        reminder.setProperty("muted", True)
        side_layout.addWidget(reminder)
        side_layout.addStretch(1)
        root.addWidget(side, 2)

        controller.recipe_validation_completed.connect(self._validation_completed)
        controller.recipe_validation_failed.connect(self._validation_failed)
        controller.recipe_validation_busy.connect(self._validation_busy)

    def _temporary_recipe(self) -> Recipe:
        return self.data.build_recipe(
            self.controller.config.operator_name,
            base_recipe=self.source_recipe,
        )

    @staticmethod
    def _independent_of_previous(record: dict, previous: list[dict]) -> bool:
        """Is this sample independent of the ones already counted?

        Validation needs several independent pieces of evidence, not the same
        evidence several times. What makes a sample independent depends on the
        fixture, and this station has two ways to be one.

        **A different physical battery.** The technician states, per sample,
        that a different part is loaded, and the statement is recorded with the
        sample. This is the only workable answer on a fixed-stop fixture -- the
        stop exists to make the pose repeatable, so requiring a different pose
        there asks the technician to defeat the fixture. It is also the better
        evidence: part-to-part variation in stamp depth, finish, and ring is
        what actually varies in production.

        **A different pose.** Where the part is free to sit differently, a
        meaningfully different position, rotation, or scale is independent on
        its own, and needs no attestation.

        Either satisfies the gate. Neither is optional: a sample that is the
        same part in the same place is the same evidence twice, and counting it
        would let a recipe qualify on one frame repeated.
        """

        if bool(record.get("different_part")):
            return True

        metrics = dict(record.get("locator_metrics", {}) or {})
        center = metrics.get("battery_center_normalized")
        rotation = float(metrics.get("rotation_deg", 0.0) or 0.0)
        scale = float(metrics.get("scale", 1.0) or 1.0)
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            return True
        cx, cy = float(center[0]), float(center[1])
        for item in previous:
            if str(item.get("disposition", "")).lower() != "pass":
                continue
            old_metrics = dict(item.get("locator_metrics", {}) or {})
            old_center = old_metrics.get("battery_center_normalized")
            if not isinstance(old_center, (list, tuple)) or len(old_center) != 2:
                continue
            distance = (
                (cx - float(old_center[0])) ** 2
                + (cy - float(old_center[1])) ** 2
            ) ** 0.5
            rotation_delta = abs(rotation - float(old_metrics.get("rotation_deg", 0.0) or 0.0))
            rotation_delta = min(rotation_delta, abs(360.0 - rotation_delta))
            scale_delta = abs(scale - float(old_metrics.get("scale", 1.0) or 1.0))
            if distance < 0.018 and rotation_delta < 2.5 and scale_delta < 0.02:
                return False
        return True

    def prepare(self) -> None:
        fingerprint = self.data.ensure_validation_matches_configuration()
        required = max(1, self.data.validation_runs_required)
        passed = min(required, self.data.validation_runs_passed)
        self.progress.setRange(0, required)
        self.progress.setValue(passed)
        self.progress.setFormat(f"{passed} / {required} PASSED")
        self.progress_text.setText(
            "VALIDATION COMPLETE" if passed >= required else "VALIDATION IN PROGRESS"
        )
        self.progress_text.setStyleSheet(
            f"font-size: 18px; font-weight: 800; color: "
            f"{GOOD if passed >= required else AMBER};"
        )

        # build_recipe() raises while the wizard data is still incomplete -- most
        # commonly before a reference has been accepted. The blocker belongs in
        # the displayed issue list, so the rest of this method must stay usable
        # without a temporary recipe rather than dereferencing an unbound name.
        temporary: Recipe | None = None
        try:
            temporary = self._temporary_recipe()
            issues = self.controller.pipeline.readiness_issues(
                temporary,
                validation_mode=True,
            )
        except Exception as exc:  # noqa: BLE001
            issues = [str(exc)]
        reference_ready = bool(
            self.data.reference_accepted
            and self.data.reference_image
            and Path(self.data.reference_image.path).is_file()
        )
        issue_text = "NONE" if not issues else "\n".join(f"• {item}" for item in issues)
        classifier_settings = (
            temporary.classifier_settings if temporary is not None else self.data.classifier_settings
        ).normalized()
        classifier_detail = self.controller.pipeline.classifier_status_for_recipe(temporary)
        ml_detail = ""
        if classifier_settings.method == "onnx_ml":
            ml_detail = (
                f"\nML model: {classifier_settings.ml_model_id or 'NOT BOUND'} "
                f"{classifier_settings.ml_model_version or ''} "
                f"[{classifier_settings.ml_model_sha256[:12] or 'NO HASH'}]"
                f"\nML acceptance: confidence ≥ {classifier_settings.ml_minimum_confidence:.0%}; "
                f"margin ≥ {classifier_settings.ml_minimum_margin:.0%}"
                f"\nCenter fallback: confidence ≥ {classifier_settings.ml_center_fallback_minimum_confidence:.0%}; "
                f"margin ≥ {classifier_settings.ml_center_fallback_minimum_margin:.0%}"
            )
        self.gates.setText(
            f"Reference accepted: {'YES' if reference_ready else 'NO'}\n"
            f"Locator: {self.controller.pipeline.battery_locator.status}\n"
            f"Classifier: {classifier_detail}{ml_detail}\n"
            f"Required counted PASS samples: {required} (distinct battery poses)\n"
            f"Configuration: {fingerprint[:12]}\n"
            f"Validation blockers:\n{issue_text}"
        )
        # A technician must always be able to acquire a fresh validation image
        # once the reference is accepted. Readiness blockers are displayed and
        # make the sample non-counting, but they must not disable the capture
        # button. This keeps recipe commissioning debuggable instead of hiding
        # the exact image/crop that caused a readiness issue.
        self.run_button.setEnabled(reference_ready and not self._busy)
        self.run_button.setText(
            "CAPTURING AND GRADING…" if self._busy else "RUN FRESH VALIDATION SAMPLE"
        )

    def run_validation(self) -> None:
        try:
            recipe = self._temporary_recipe()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Recipe is not ready for validation", str(exc))
            return
        issues = self.controller.pipeline.readiness_issues(recipe, validation_mode=True)
        if issues:
            # Continue with acquisition so the technician gets real evidence and
            # exact ROI crops. The pipeline will return NOT READY and the sample
            # will not count until these blockers are resolved.
            self.result.setText(
                "CAPTURE WILL NOT COUNT UNTIL READY — " + "; ".join(issues)
            )
            self.result.setStyleSheet(
                f"padding: 12px; background: {SURFACE_STRONG}; border: 1px solid {AMBER}; "
                f"font-size: 16px; font-weight: 700; color: {AMBER};"
            )
        self._request_active = True
        if not self.controller.validate_recipe_sample(recipe):
            self._request_active = False
            QMessageBox.information(
                self,
                "Camera is occupied",
                "Wait for the current camera operation or inspection to finish, then validate again.",
            )

    def _validation_busy(self, busy: bool) -> None:
        if not self._request_active and busy:
            return
        self._busy = busy
        self.prepare()

    def _validation_failed(self, message: str) -> None:
        if not self._request_active:
            return
        self._busy = False
        self._request_active = False
        self.result.setText(f"VALIDATION CAPTURE FAILED\n{message}")
        self.result.setStyleSheet(
            f"padding: 12px; background: {BAD_BG}; border: 1px solid {BAD}; "
            f"color: {BAD}; font-size: 16px; font-weight: 700;"
        )
        self.prepare()

    def _validation_completed(self, payload: object) -> None:
        if not self._request_active or not isinstance(payload, InspectionResult):
            return
        self._request_active = False
        self._last_result = payload
        image_path = Path(payload.full_image_path) if payload.full_image_path else None
        self.image.set_image(image_path if image_path and image_path.is_file() else None)
        polygons: list[PolygonOverlaySpec] = []
        if payload.battery_polygon:
            polygons.append(
                PolygonOverlaySpec(
                    "battery",
                    payload.battery_polygon,
                    "REGISTERED BATTERY",
                    BLUE,
                    line_width=3,
                )
            )
        for index, terminal in enumerate(payload.terminals, start=1):
            color = ROLE_POSITIVE if terminal.role == TerminalRole.POSITIVE else ROLE_NEGATIVE
            if terminal.terminal_polygon:
                polygons.append(
                    PolygonOverlaySpec(
                        f"{terminal.terminal_key}-terminal",
                        terminal.terminal_polygon,
                        f"{index} {terminal.role.display}",
                        color,
                        line_width=3,
                    )
                )
            if terminal.marking_polygon:
                polygons.append(
                    PolygonOverlaySpec(
                        f"{terminal.terminal_key}-marking",
                        terminal.marking_polygon,
                        "MARKING CIRCLE",
                        ROI_MARKING,
                        dashed=True,
                        line_width=2,
                    )
                )
        self.image.set_polygon_overlays(polygons)
        self.image.set_overlays([])

        record = {
            "inspection_id": payload.inspection_id,
            "cycle_id": payload.cycle_id,
            "frame_id": payload.frame_id,
            "captured_at_utc": payload.captured_at_utc,
            "disposition": payload.disposition.value,
            "reason": payload.reason,
            "evidence_directory": payload.evidence_directory,
            "locator_metrics": dict(payload.locator_metrics),
            "terminals": [terminal.to_dict() for terminal in payload.terminals],
        }
        # Recorded against the sample, so the evidence says on what basis it
        # was counted rather than leaving that to be inferred later.
        record["different_part"] = bool(self.different_part.isChecked())
        distinct = self._independent_of_previous(record, self.data.validation_records)
        if payload.disposition == InspectionDisposition.PASS and not distinct:
            record["disposition"] = "duplicate_sample"
            record["reason"] = (
                "SAME PART IN THE SAME POSITION — LOAD A DIFFERENT BATTERY AND "
                "CONFIRM IT, OR MOVE THIS ONE"
            )
        self.data.add_validation_record(record)

        counted = record["disposition"] == "pass"
        tone = GOOD if counted else BAD
        if payload.disposition == InspectionDisposition.PASS and not distinct:
            tone = AMBER
        if counted:
            # Cleared after every counted sample, so confirming a part change
            # is a deliberate act each time rather than a box left ticked.
            self.different_part.setChecked(False)
        self.result.setText(
            f"{payload.disposition.display}\n{record['reason']}\n"
            f"Frame: {payload.frame_id or '—'}"
        )
        self.result.setStyleSheet(
            f"padding: 12px; background: {SURFACE_STRONG}; border: 1px solid {tone}; "
            f"color: {tone}; font-size: 16px; font-weight: 700;"
        )
        metrics = payload.locator_metrics
        self.metrics.setText(
            "Locator metrics\n"
            f"matches={metrics.get('good_matches', '—')}  "
            f"inliers={metrics.get('inliers', '—')}  "
            f"ratio={float(metrics.get('inlier_ratio', 0.0) or 0.0):.1%}\n"
            f"error={float(metrics.get('median_reprojection_error_px', 0.0) or 0.0):.2f}px  "
            f"rotation={float(metrics.get('rotation_deg', 0.0) or 0.0):.1f}°  "
            f"scale={float(metrics.get('scale', 0.0) or 0.0):.3f}"
        )
        terminal_lines = []
        for terminal in payload.terminals:
            class_metrics = dict(terminal.classification_metrics or {})
            detected_text = terminal.detected_marking.display
            if (
                terminal.detected_marking == Marking.UNREADABLE
                and bool(class_metrics.get("ml_model_id"))
            ):
                detected_text = "NO DECISION"
            margin = class_metrics.get("ml_margin")
            required_conf = class_metrics.get("ml_required_confidence")
            required_margin = class_metrics.get("ml_required_margin")
            ml_gate = ""
            if margin is not None:
                ml_gate = (
                    f"; ML margin {float(margin):.1%}"
                    + (f" (need ≥ {float(required_margin):.1%})" if required_margin is not None else "")
                    + (f"; confidence gate ≥ {float(required_conf):.1%}" if required_conf is not None else "")
                )
            face_text = (
                f"face {'PRESENT' if terminal.terminal_face_present else terminal.terminal_face_status.replace('TERMINAL_FACE_', '')} "
                f"({terminal.terminal_face_confidence:.1%})"
                if terminal.terminal_face_evaluated
                else "face NOT EVALUATED"
            )
            terminal_lines.append(
                f"{terminal.role.display}: expected {terminal.expected_marking.display}, "
                f"detected {detected_text} "
                f"({terminal.marking_confidence:.1%}){ml_gate}; "
                f"{face_text}; "
                f"finish {terminal.detected_finish.display} / expected "
                f"{terminal.expected_finish.display} "
                f"({'PASS' if terminal.finish_pass else 'FAIL'}); "
                f"classifier {terminal.classification_status or '—'}; "
                f"ring {'PASS' if terminal.ring_pass else 'FAIL'}"
            )
        self.terminals.setText("\n".join(terminal_lines) or "Terminal results: none")
        self.image_caption.setText(
            f"{payload.disposition.display} — {payload.reason} — {payload.frame_width} × {payload.frame_height}"
        )
        self.prepare()

    def can_continue(self) -> tuple[bool, str]:
        # Incomplete recipes may still be saved as drafts. Activation remains
        # disabled on the review page until all real validation samples pass.
        return True, ""

    def detach(self) -> None:
        for signal, slot in (
            (self.controller.recipe_validation_completed, self._validation_completed),
            (self.controller.recipe_validation_failed, self._validation_failed),
            (self.controller.recipe_validation_busy, self._validation_busy),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass


class ReviewPage(WizardPage):
    action_changed = Signal(bool)

    def __init__(self, data: RecipeWizardData) -> None:
        super().__init__()
        self.data = data
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(12)
        summary = PanelFrame()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(18, 16, 18, 16)
        title = QLabel("Step 7: Review and save")
        title.setObjectName("PanelTitle")
        summary_layout.addWidget(title)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-size: 16px; line-height: 1.3;")
        summary_layout.addWidget(self.summary_label)
        summary_layout.addStretch(1)
        root.addWidget(summary, 3)

        action = PanelFrame()
        action_layout = QVBoxLayout(action)
        action_layout.setContentsMargins(18, 16, 18, 16)
        action_title = QLabel("SAVE ACTION")
        action_title.setObjectName("PanelTitle")
        action_layout.addWidget(action_title)
        self.state = QLabel("SAVE AS DRAFT")
        action_layout.addWidget(self.state)
        self.activate = QCheckBox("Activate this validated revision for production")
        self.activate.toggled.connect(self._update_action)
        action_layout.addWidget(self.activate)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        action_layout.addWidget(self.warning)
        action_layout.addStretch(1)
        self.status = QLabel()
        action_layout.addWidget(self.status)
        root.addWidget(action, 2)

    def _validation_complete(self) -> bool:
        return self.data.validation_complete

    def _update_action(self) -> None:
        production = self._validation_complete() and self.activate.isChecked()
        if production:
            self.state.setText("SAVE FOR PRODUCTION")
            self.state.setStyleSheet(
                f"color: {GOOD}; font-size: 21px; font-weight: 800;"
            )
            self.warning.setText(
                "This immutable revision is validated, so the station will grade "
                "against it as soon as it is saved: the PLC names the product on "
                "every trigger and the newest validated revision of that product "
                "wins. Earlier revisions remain stored for rollback."
            )
            self.warning.setStyleSheet(
                f"color: {GOOD}; padding: 10px; background: {GOOD_BG}; "
                f"border: 1px solid {GOOD};"
            )
        else:
            self.state.setText("SAVE AS DRAFT")
            self.state.setStyleSheet(
                f"color: {AMBER}; font-size: 21px; font-weight: 800;"
            )
            self.warning.setText(
                "A draft is never used to grade a part. It will be stored and "
                "ignored by both PLC and manual triggers until it is reopened, "
                "validated, and saved for production."
            )
            self.warning.setStyleSheet(
                f"color: {AMBER}; padding: 10px; background: {AMBER_BG}; "
                f"border: 1px solid {AMBER};"
            )
        self.action_changed.emit(production)

    def prepare(self) -> None:
        self.data.ensure_validation_matches_configuration()
        complete = self._validation_complete()
        self.activate.setEnabled(complete)
        if not complete:
            self.activate.setChecked(False)
        reference = self.data.reference_image
        negative = self.data.expected_markings["negative"].display
        positive = self.data.expected_markings["positive"].display
        negative_finish = self.data.expected_finishes["negative"].display
        positive_finish = self.data.expected_finishes["positive"].display
        self.summary_label.setText(
            f"<b>Recipe:</b> {self.data.recipe_number} — {self.data.name}<br>"
            f"<b>Part number:</b> {self.data.part_number}<br>"
            f"<b>Reference:</b> {reference.width_px if reference else 0} × {reference.height_px if reference else 0} "
            f"captured {reference.captured_at_utc if reference else 'not captured'}<br><br>"
            f"<b>Negative terminal</b><br>Expected marking: {negative}<br>"
            f"Expected finish: {negative_finish}<br>"
            f"Red ring required: {'YES' if self.data.red_ring_required['negative'] else 'NO'}<br><br>"
            f"<b>Positive terminal</b><br>Expected marking: {positive}<br>"
            f"Expected finish: {positive_finish}<br>"
            f"Red ring required: {'YES' if self.data.red_ring_required['positive'] else 'NO'}<br><br>"
            f"<b>Locator:</b> {self.data.locator_settings.method}<br>"
            f"<b>Classifier:</b> {self.data.classifier_settings.method}<br>"
            f"<b>Validation:</b> {self.data.validation_runs_passed} / "
            f"{self.data.validation_runs_required} real samples passed"
        )
        self.status.setText(
            "●  PRODUCTION VALIDATION COMPLETE"
            if complete
            else "●  VALIDATION INCOMPLETE — DRAFT ONLY"
        )
        self.status.setStyleSheet(
            f"color: {GOOD if complete else AMBER}; "
            "font-size: 16px; font-weight: 800;"
        )
        self._update_action()

    def commit(self) -> None:
        self.data.activate_on_finish = bool(
            self.activate.isEnabled() and self.activate.isChecked()
        )


class RecipeWizardDialog(QDialog):
    recipe_ready = Signal(object, bool)

    STEPS = [
        "Reference",
        "Identify",
        "Battery",
        "Terminals",
        "Polarity",
        "Validate",
        "Complete",
    ]

    def __init__(
        self,
        *,
        controller: AppController,
        username: str,
        recipe: Recipe | None = None,
        initial_reference_action: str = "choose",
        template_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.source_recipe = recipe
        self.template_mode = bool(template_mode)
        self.setWindowTitle(
            "New Battery Recipe from Import"
            if self.template_mode
            else ("Edit Battery Recipe" if recipe else "New Battery Recipe")
        )
        self.setObjectName("AppRoot")
        self.resize(1500, 900)
        self.setMinimumSize(1180, 760)
        self.data = RecipeWizardData.from_recipe(recipe) if recipe else RecipeWizardData()
        if recipe is None or self.template_mode:
            # A new recipe adopts the station's requirement. An existing one
            # keeps the count it was validated against, so reopening a recipe
            # does not quietly restate how thoroughly it was qualified.
            self.data.validation_runs_required = max(
                1, int(controller.config.validation_runs_required)
            )
        if recipe is None or self.template_mode:
            self.data.recipe_number = controller.next_recipe_number()
        if controller.config.ml.use_for_new_revisions:
            try:
                self.data.classifier_settings = controller.ml_classifier_settings_for_revision(
                    self.data.classifier_settings
                )
                model_info = controller.ml_model_info(require_runtime=False)
                if str(model_info.get("input_crop_contract", "")) == TAUGHT_CIRCLE_CROP_CONTRACT:
                    self.data.marking_roi_shapes["negative"] = CIRCLE_ROI_SHAPE
                    self.data.marking_roi_shapes["positive"] = CIRCLE_ROI_SHAPE
                self.data.reset_validation()
            except ValueError:
                # Keep the legacy classifier available until an engineer installs
                # a valid ONNX package.  Recipe creation must never become
                # impossible merely because ML has not been commissioned yet.
                pass
        self.username = username
        self.created_recipe: Recipe | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        header = QHBoxLayout()
        heading = (
            f"RECIPE WIZARD — IMPORT {recipe.name} / CREATE REVISION 1"
            if self.template_mode and recipe is not None
            else (
                "RECIPE WIZARD — NEW BATTERY MODEL"
                if recipe is None
                else f"RECIPE WIZARD — EDIT {recipe.name} / CREATE REVISION {recipe.revision + 1}"
            )
        )
        title = QLabel(heading)
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        user = QLabel(f"Current user: {username}")
        user.setProperty("muted", True)
        header.addWidget(user)
        root.addLayout(header)
        self.step_indicator = StepIndicator(self.STEPS)
        root.addWidget(self.step_indicator)

        self.stack = QStackedWidget()
        self.pages: list[WizardPage] = [
            ReferenceCapturePage(self.data, controller, recipe),
            IdentifyPage(
                self.data,
                number_locked=recipe is not None and not self.template_mode,
            ),
            DefineBatteryPage(self.data),
            DefineTerminalsPage(self.data),
            PolarityPage(self.data, controller),
            ReadinessPage(self.data, controller, recipe),
            ReviewPage(self.data),
        ]
        for page in self.pages:
            self.stack.addWidget(page)
        review_page = self.pages[-1]
        if isinstance(review_page, ReviewPage):
            review_page.action_changed.connect(
                lambda activate: self.next_button.setText(
                    "SAVE FOR PRODUCTION" if activate else "SAVE DRAFT"
                )
            )
        root.addWidget(self.stack, 1)

        navigation = QHBoxLayout()
        self.cancel_button = QPushButton("CANCEL")
        self.cancel_button.clicked.connect(self.reject)
        self.back_button = QPushButton("BACK")
        self.back_button.clicked.connect(self.go_back)
        self.next_button = QPushButton("NEXT  →")
        self.next_button.setObjectName("PrimaryButton")
        self.next_button.clicked.connect(self.go_next)
        navigation.addWidget(self.cancel_button)
        navigation.addStretch(1)
        navigation.addWidget(self.back_button)
        navigation.addWidget(self.next_button)
        root.addLayout(navigation)
        self._show_page(0)
        reference_page = self.pages[0]
        if isinstance(reference_page, ReferenceCapturePage):
            action = str(initial_reference_action or "choose").strip().lower()
            if action == "keep" and recipe is not None:
                reference_page.keep_existing()
            elif action == "capture":
                QTimer.singleShot(150, reference_page.capture_new)

    def _show_page(self, index: int) -> None:
        index = max(0, min(index, len(self.pages) - 1))
        self.stack.setCurrentIndex(index)
        self.step_indicator.set_current_index(index)
        self.pages[index].prepare()
        self.back_button.setDisabled(index == 0)
        if index == len(self.pages) - 1:
            self.next_button.setText(
                "SAVE FOR PRODUCTION"
                if self.data.activate_on_finish
                else "SAVE DRAFT"
            )
        else:
            self.next_button.setText("NEXT  →")

    def go_back(self) -> None:
        current = self.stack.currentIndex()
        if current > 0:
            self._show_page(current - 1)

    def go_next(self) -> None:
        current = self.stack.currentIndex()
        page = self.pages[current]
        page.commit()
        ok, message = page.can_continue()
        if not ok:
            QMessageBox.warning(self, "Recipe step incomplete", message)
            return
        if current < len(self.pages) - 1:
            self._show_page(current + 1)
            return
        try:
            draft_recipe = self.data.build_recipe(
                self.username,
                base_recipe=self.source_recipe,
            )
            self.created_recipe = self.controller.save_recipe(
                draft_recipe,
                activate=self.data.activate_on_finish,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Recipe cannot be saved", str(exc))
            return
        self.recipe_ready.emit(self.created_recipe, self.data.activate_on_finish)
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        for page in self.pages:
            detach = getattr(page, "detach", None)
            if callable(detach):
                detach()
        super().done(result)
