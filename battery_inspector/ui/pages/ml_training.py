from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import Qt
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
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.ml_training import (
    MlTrainingParameters,
    REVIEW_LABELS,
    TRAINING_LABELS,
)
from battery_inspector.models import NormalizedRect, ReferenceCapture
from battery_inspector.paths import is_frozen
from battery_inspector.roi_geometry import CIRCLE_ROI_SHAPE, coerce_circle_rect
from battery_inspector.ui.image_widgets import CropPreview, OverlaySpec, RoiEditor
from battery_inspector.ui.palette import (
    AMBER,
    AMBER_BG,
    BAD,
    BLUE,
    BORDER,
    GOOD,
    ROI_MARKING,
    SURFACE_ALT,
    TEXT_MUTED,
)
from battery_inspector.ui.widgets import LabeledValue, PageNavigator, PanelFrame, StepIndicator


class MlTrainingPage(QWidget):
    """Guided, camera-first ML dataset capture, training, evaluation, and deploy UI.

    The technician never needs to browse an inspection evidence folder. Samples
    are captured from a fresh camera frame, labeled explicitly, and cropped with
    a visible adjustable circle that is intended to contain only the flat metal
    terminal top.
    """

    STEPS = ["Capture", "Review", "Prepare", "Train", "Deploy"]

    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.current_capture: ReferenceCapture | None = None
        self.roi_drafts: list[dict[str, Any]] = [
            {"key": "ml_top_1", "rect": NormalizedRect(0.20, 0.34, 0.18, 0.22), "label": "plus"},
            {"key": "ml_top_2", "rect": NormalizedRect(0.62, 0.34, 0.18, 0.22), "label": "minus"},
        ]
        self.active_roi_key = "ml_top_1"
        self._roi_serial = 2
        self.last_saved_batch_ids: list[str] = []
        self.prepared_summary: dict[str, Any] | None = None
        self.training_result: dict[str, Any] | None = controller.ml_training_latest_result()
        self.environment_info: dict[str, Any] | None = None
        self._capture_busy = False
        self._training_busy = False
        self._step = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("ML TRAINING")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.mode_status = QLabel("GUIDED CAMERA DATASET")
        self.mode_status.setProperty("muted", True)
        header.addWidget(self.mode_status)
        self.latest_candidate_button = QPushButton("LATEST CANDIDATE")
        self.latest_candidate_button.clicked.connect(lambda: self.set_step(4))
        self.latest_candidate_button.setVisible(self.training_result is not None)
        header.addWidget(self.latest_candidate_button)
        root.addLayout(header)

        self.steps = StepIndicator(self.STEPS)
        root.addWidget(self.steps)

        self.stack = QStackedWidget()
        self.capture_page = self._build_capture_page()
        self.review_page = self._build_review_page()
        self.prepare_page = self._build_prepare_page()
        self.train_page = self._build_train_page()
        self.deploy_page = self._build_deploy_page()
        for page in (
            self.capture_page,
            self.review_page,
            self.prepare_page,
            self.train_page,
            self.deploy_page,
        ):
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)

        footer = QHBoxLayout()
        self.back_button = QPushButton("◀  BACK")
        self.next_button = QPushButton("NEXT  ▶")
        self.next_button.setObjectName("PrimaryButton")
        self.back_button.clicked.connect(self.go_back)
        self.next_button.clicked.connect(self.go_next)
        footer.addWidget(self.back_button)
        footer.addStretch(1)
        self.step_message = QLabel()
        self.step_message.setProperty("muted", True)
        self.step_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.addWidget(self.step_message, 1)
        footer.addStretch(1)
        footer.addWidget(self.next_button)
        root.addLayout(footer)

        controller.ml_training_capture_completed.connect(self._capture_completed)
        controller.ml_training_capture_failed.connect(self._capture_failed)
        controller.ml_training_capture_busy.connect(self._capture_busy_changed)
        controller.ml_training_samples_changed.connect(self._samples_changed)
        controller.ml_training_progress.connect(self._training_progress)
        controller.ml_training_completed.connect(self._training_completed)
        controller.ml_training_failed.connect(self._training_failed)
        controller.ml_training_busy.connect(self._training_busy_changed)
        controller.ml_model_changed.connect(lambda _payload: self._refresh_deploy_status())

        self.refresh_counts()
        self.set_step(0)

    def _samples_changed(self, _payload: object) -> None:
        # The prepared train/val/test folders are a snapshot. Any persistent
        # sample add/remove/relabel invalidates that snapshot so the next
        # training run cannot accidentally use stale images or labels.
        self.prepared_summary = None
        if hasattr(self, "dataset_status"):
            self.dataset_status.setText(
                "TRAINING DATA CHANGED — select PREPARE DATASET again before the next training run."
            )
            self.dataset_status.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        self.refresh_counts()

    # ------------------------------------------------------------------
    # Step 1: capture and label
    # ------------------------------------------------------------------
    MAX_CAPTURE_ROIS = 6

    @staticmethod
    def _label_text(label: str) -> str:
        return str(label or "").replace("_", " ").upper()

    def _build_capture_page(self) -> QWidget:
        page = QWidget()
        root = QHBoxLayout(page)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)

        image_panel = PanelFrame()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("CAPTURE MULTIPLE TERMINAL TOPS FROM ONE FRAME")
        title.setObjectName("PanelTitle")
        image_layout.addWidget(title)
        instruction = QLabel(
            "Capture one fresh full-resolution frame, then draw a separate dashed CIRCLE around every metal terminal top you want to label. "
            "Each circle is converted to a masked square ML image and saved from the SAME camera frame. "
            "Use INVALID MARKING for a present terminal face whose observed pattern is not a valid PLUS, MINUS, or BLANK."
        )
        instruction.setWordWrap(True)
        instruction.setProperty("muted", True)
        image_layout.addWidget(instruction)
        self.capture_editor = RoiEditor()
        self.capture_editor.setMinimumSize(650, 360)
        self.capture_editor.roi_changed.connect(self._roi_changed)
        self.capture_editor.selection_changed.connect(self._roi_selected)
        self._sync_editor_rois()
        image_layout.addWidget(self.capture_editor, 1)
        self.capture_meta = QLabel("NO FRESH FRAME")
        self.capture_meta.setProperty("muted", True)
        image_layout.addWidget(self.capture_meta)
        root.addWidget(image_panel, 7)

        side = PanelFrame()
        side.setMinimumWidth(460)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(14, 12, 14, 12)
        side_layout.setSpacing(7)
        side_title = QLabel("CAPTURE BATCH")
        side_title.setObjectName("PanelTitle")
        side_layout.addWidget(side_title)

        tag_form = QFormLayout()
        self.collection_tag = QLineEdit()
        self.collection_tag.setPlaceholderText("Optional, e.g. Group31 / supplier A / terminal style B")
        tag_form.addRow("Battery / terminal family", self.collection_tag)
        side_layout.addLayout(tag_form)

        self.crop_safety = QLabel(
            "ROI RULE: draw each dashed circle around only the flat metal terminal face and stamp. "
            "Keep the red ring, molded case +/− symbols, washer, and outer hardware outside every circle. "
            "The application converts each circle to a square and neutralizes everything outside the circle before training. "
            "Do not label a missing terminal as INVALID MARKING; missing/invalid terminal faces are handled by the separate physical-input gate."
        )
        self.crop_safety.setWordWrap(True)
        self.crop_safety.setStyleSheet(
            f"color: {AMBER}; background: {AMBER_BG}; border: 1px solid {AMBER}; padding: 7px;"
        )
        side_layout.addWidget(self.crop_safety)

        queue_header = QHBoxLayout()
        queue_title = QLabel("TERMINAL-TOP CIRCLES ON THIS FRAME")
        queue_title.setObjectName("PanelTitle")
        queue_header.addWidget(queue_title)
        queue_header.addStretch(1)
        self.active_roi_label = QLabel("ROI 1 — PLUS")
        self.active_roi_label.setProperty("muted", True)
        queue_header.addWidget(self.active_roi_label)
        side_layout.addLayout(queue_header)
        self.roi_queue_layout = QVBoxLayout()
        self.roi_queue_layout.setContentsMargins(0, 0, 0, 0)
        self.roi_queue_layout.setSpacing(3)
        side_layout.addLayout(self.roi_queue_layout)

        queue_actions = QGridLayout()
        queue_actions.setHorizontalSpacing(6)
        queue_actions.setVerticalSpacing(4)
        self.add_roi_button = QPushButton("+ ADD CIRCLE")
        self.add_roi_button.clicked.connect(self._add_roi)
        self.roi_smaller = QPushButton("− SMALLER CIRCLE")
        self.roi_larger = QPushButton("+ LARGER CIRCLE")
        self.redraw_roi = QPushButton("REDRAW ACTIVE CIRCLE")
        self.roi_smaller.clicked.connect(lambda: self._nudge_roi(0.90))
        self.roi_larger.clicked.connect(lambda: self._nudge_roi(1.10))
        self.redraw_roi.clicked.connect(
            lambda: self.capture_editor.begin_redraw(self.active_roi_key)
        )
        queue_actions.addWidget(self.add_roi_button, 0, 0, 1, 2)
        queue_actions.addWidget(self.roi_smaller, 1, 0)
        queue_actions.addWidget(self.roi_larger, 1, 1)
        queue_actions.addWidget(self.redraw_roi, 2, 0, 1, 2)
        side_layout.addLayout(queue_actions)

        capture_actions = QHBoxLayout()
        self.capture_button = QPushButton("CAPTURE FRESH FRAME")
        self.capture_button.setObjectName("PrimaryButton")
        self.capture_button.clicked.connect(self.capture_fresh_frame)
        self.save_batch_button = QPushButton("SAVE ALL CIRCLES")
        self.save_batch_button.clicked.connect(self.save_capture_batch)
        self.save_batch_button.setEnabled(False)
        capture_actions.addWidget(self.capture_button)
        capture_actions.addWidget(self.save_batch_button)
        side_layout.addLayout(capture_actions)

        self.undo_sample_button = QPushButton("UNDO LAST CAPTURE BATCH")
        self.undo_sample_button.clicked.connect(self.undo_last_batch)
        self.undo_sample_button.setEnabled(False)
        side_layout.addWidget(self.undo_sample_button)

        self.capture_status = QLabel("Capture a fresh frame to begin. Two circles are pre-created for the common PLUS / MINUS case.")
        self.capture_status.setWordWrap(True)
        self.capture_status.setProperty("muted", True)
        side_layout.addWidget(self.capture_status)

        self.capture_count_summary = QLabel("DATASET — no samples captured yet")
        self.capture_count_summary.setWordWrap(True)
        self.capture_count_summary.setProperty("muted", True)
        side_layout.addWidget(self.capture_count_summary)
        side_layout.addStretch(1)
        root.addWidget(side, 3)

        self._rebuild_roi_rows()
        self._update_save_batch_enabled()
        return page

    def _draft_for_key(self, key: str) -> dict[str, Any] | None:
        return next((draft for draft in self.roi_drafts if draft["key"] == key), None)

    def _sync_editor_rois(self) -> None:
        overlays: list[OverlaySpec] = []
        for index, draft in enumerate(self.roi_drafts, start=1):
            label = self._label_text(str(draft.get("label") or "UNASSIGNED"))
            overlays.append(
                OverlaySpec(
                    str(draft["key"]),
                    draft["rect"],
                    f"ROI {index}  {label}",
                    ROI_MARKING,
                    dashed=True,
                    line_width=3,
                    shape=CIRCLE_ROI_SHAPE,
                )
            )
        self.capture_editor.set_editable_rois(overlays)
        if self.active_roi_key and self._draft_for_key(self.active_roi_key) is not None:
            self.capture_editor.set_active_key(self.active_roi_key)

    def _rebuild_roi_rows(self) -> None:
        if not hasattr(self, "roi_queue_layout"):
            return
        while self.roi_queue_layout.count():
            item = self.roi_queue_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for index, draft in enumerate(self.roi_drafts, start=1):
            key = str(draft["key"])
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(5)
            select = QPushButton(f"{'▶ ' if key == self.active_roi_key else ''}ROI {index}")
            select.setMinimumWidth(76)
            select.clicked.connect(lambda _checked=False, roi_key=key: self._select_roi(roi_key))
            layout.addWidget(select)

            combo = QComboBox()
            combo.addItem("SELECT CLASS", "")
            combo.addItem("+  PLUS", "plus")
            combo.addItem("−  MINUS", "minus")
            combo.addItem("BLANK", "blank")
            combo.addItem("INVALID MARKING", "invalid_marking")
            current_label = str(draft.get("label") or "")
            combo_index = combo.findData(current_label)
            combo.setCurrentIndex(max(0, combo_index))
            combo.currentIndexChanged.connect(
                lambda _index, roi_key=key, source=combo: self._roi_label_changed(
                    roi_key, str(source.currentData() or "")
                )
            )
            layout.addWidget(combo, 1)

            remove = QPushButton("REMOVE")
            remove.setEnabled(len(self.roi_drafts) > 1)
            remove.clicked.connect(lambda _checked=False, roi_key=key: self._remove_roi(roi_key))
            layout.addWidget(remove)
            self.roi_queue_layout.addWidget(row)

        self.add_roi_button.setEnabled(len(self.roi_drafts) < self.MAX_CAPTURE_ROIS)
        active = self._draft_for_key(self.active_roi_key)
        if active is not None:
            active_index = self.roi_drafts.index(active) + 1
            label = self._label_text(str(active.get("label") or "UNASSIGNED"))
            self.active_roi_label.setText(f"ROI {active_index} — {label}")
        self._update_save_batch_enabled()

    def _select_roi(self, key: str) -> None:
        if self._draft_for_key(key) is None:
            return
        self.active_roi_key = key
        self.capture_editor.set_active_key(key)
        self._rebuild_roi_rows()

    def _roi_selected(self, key: str) -> None:
        if self._draft_for_key(key) is None:
            return
        if key != self.active_roi_key:
            self.active_roi_key = key
            self._rebuild_roi_rows()

    def _roi_label_changed(self, key: str, label: str) -> None:
        draft = self._draft_for_key(key)
        if draft is None:
            return
        draft["label"] = label
        self._sync_editor_rois()
        self._rebuild_roi_rows()

    def _add_roi(self) -> None:
        if len(self.roi_drafts) >= self.MAX_CAPTURE_ROIS:
            QMessageBox.information(
                self,
                "ROI limit",
                f"A single capture supports up to {self.MAX_CAPTURE_ROIS} terminal-top circles. Capture another frame for additional terminals.",
            )
            return
        self._roi_serial += 1
        active = self._draft_for_key(self.active_roi_key)
        base = active["rect"] if active is not None else NormalizedRect(0.40, 0.40, 0.18, 0.22)
        shifted = NormalizedRect(
            min(0.80, max(0.02, base.x + 0.08)),
            min(0.78, max(0.02, base.y + 0.08)),
            base.width,
            base.height,
        ).clamped()
        key = f"ml_top_{self._roi_serial}"
        self.roi_drafts.append({"key": key, "rect": shifted, "label": ""})
        self.active_roi_key = key
        self._sync_editor_rois()
        self._rebuild_roi_rows()

    def _remove_roi(self, key: str) -> None:
        if len(self.roi_drafts) <= 1:
            return
        self.roi_drafts = [draft for draft in self.roi_drafts if draft["key"] != key]
        if self.active_roi_key == key:
            self.active_roi_key = str(self.roi_drafts[0]["key"])
        self._sync_editor_rois()
        self._rebuild_roi_rows()

    def capture_fresh_frame(self) -> None:
        if not self.controller.capture_ml_training_frame():
            QMessageBox.information(
                self,
                "Camera occupied",
                "Wait for the current inspection, recipe capture, or camera operation to finish, then capture again.",
            )

    def _capture_completed(self, payload: object) -> None:
        if not isinstance(payload, ReferenceCapture):
            self._capture_failed("Camera returned invalid ML capture metadata")
            return
        self.current_capture = payload
        self.capture_editor.set_image(payload.path)
        # Circle ROIs must be square in source-image pixels, not normalized
        # coordinates. Re-coerce the persistent layout for the detected camera
        # resolution whenever a new frame arrives.
        for draft in self.roi_drafts:
            draft["rect"] = coerce_circle_rect(
                draft["rect"],
                payload.width_px,
                payload.height_px,
            )
        # Preserve the current multi-ROI layout between frames. This makes it fast
        # to collect repeated PLUS/MINUS pairs while moving/rotating batteries.
        self._sync_editor_rois()
        quality = dict(payload.quality or {})
        self.capture_meta.setText(
            f"Fresh frame {payload.frame_id or payload.frame_sequence}  |  {payload.width_px} x {payload.height_px}  |  "
            f"Image quality {quality.get('status', quality.get('quality', 'UNKNOWN'))}  |  {len(self.roi_drafts)} circle(s)"
        )
        self.capture_status.setText(
            "Fresh frame captured. Position every dashed circle over a terminal top, assign the actual class for each circle, then SAVE ALL CIRCLES. "
            "All masked-square ML images are stored together under this frame's capture ID."
        )
        self.capture_status.setStyleSheet("")
        self._update_save_batch_enabled()

    def _capture_failed(self, message: str) -> None:
        self.capture_status.setText(f"CAPTURE FAILED — {message}")
        self.capture_status.setStyleSheet(f"color: {BAD}; font-weight: 700;")

    def _capture_busy_changed(self, busy: bool) -> None:
        self._capture_busy = bool(busy)
        self.capture_button.setEnabled(not busy and not self._training_busy)
        self._update_save_batch_enabled()
        if busy:
            self.capture_status.setText("CAPTURING A FRESH FULL-RESOLUTION FRAME…")
            self.capture_status.setStyleSheet(f"color: {BLUE}; font-weight: 700;")
        else:
            self.capture_status.setStyleSheet("")

    def _roi_changed(self, key: str, rect: object) -> None:
        if not isinstance(rect, NormalizedRect):
            return
        draft = self._draft_for_key(key)
        if draft is None:
            return
        draft["rect"] = rect
        if key == self.active_roi_key:
            self._update_save_batch_enabled()

    def _nudge_roi(self, scale: float) -> None:
        if self.active_roi_key:
            self.capture_editor.nudge_size(self.active_roi_key, scale)

    def _batch_is_labeled(self) -> bool:
        return bool(self.roi_drafts) and all(
            str(draft.get("label") or "") in TRAINING_LABELS for draft in self.roi_drafts
        )

    def _update_save_batch_enabled(self, *_args) -> None:
        enabled = (
            self.current_capture is not None
            and self._batch_is_labeled()
            and not self._capture_busy
            and not self._training_busy
        )
        self.save_batch_button.setEnabled(enabled)
        self.save_batch_button.setText(f"SAVE ALL CIRCLES ({len(self.roi_drafts)})")

    def save_capture_batch(self) -> None:
        if self.current_capture is None:
            QMessageBox.information(self, "No capture", "Capture a fresh camera frame first.")
            return
        unlabeled = [index + 1 for index, draft in enumerate(self.roi_drafts) if str(draft.get("label") or "") not in TRAINING_LABELS]
        if unlabeled:
            QMessageBox.warning(
                self,
                "ROI class required",
                "Choose PLUS, MINUS, BLANK, or INVALID MARKING for every circle "
                "before saving. "
                f"Unassigned ROI(s): {', '.join(str(item) for item in unlabeled)}.",
            )
            return
        items = [
            (str(draft["key"]), draft["rect"], str(draft["label"]))
            for draft in self.roi_drafts
        ]
        try:
            summary = self.controller.save_ml_training_samples(
                self.current_capture,
                items,
                collection_tag=self.collection_tag.text().strip(),
                roi_shape=CIRCLE_ROI_SHAPE,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Capture batch not saved", str(exc))
            return
        saved = list(summary.get("saved_samples") or [])
        new_items = [item for item in saved if not bool(item.get("duplicate"))]
        duplicates = [item for item in saved if bool(item.get("duplicate"))]
        self.last_saved_batch_ids = [str(item.get("sample_id", "")) for item in new_items if item.get("sample_id")]
        self.undo_sample_button.setEnabled(bool(self.last_saved_batch_ids))
        if new_items:
            classes = ", ".join(
                self._label_text(str(item.get("label", ""))) for item in new_items
            )
            self.capture_status.setText(
                f"SAVED {len(new_items)} NEW ROI(S) FROM ONE FRAME ({classes}). "
                f"{len(duplicates)} duplicate ROI(s) were already in the dataset. Move/rotate the battery and CAPTURE FRESH FRAME for more variation."
            )
            self.capture_status.setStyleSheet(f"color: {GOOD}; font-weight: 700;")
        else:
            self.capture_status.setText(
                f"ALL {len(duplicates)} ROI(S) WERE DUPLICATES. No new training samples were added; capture a changed battery pose or terminal condition."
            )
            self.capture_status.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        self.refresh_counts()

    def undo_last_batch(self) -> None:
        if not self.last_saved_batch_ids:
            return
        removed = 0
        for sample_id in list(self.last_saved_batch_ids):
            if self.controller.remove_ml_training_sample(sample_id):
                removed += 1
        self.last_saved_batch_ids = []
        self.undo_sample_button.setEnabled(False)
        self.capture_status.setText(f"Removed {removed} sample(s) from the last saved capture batch.")
        self.capture_status.setStyleSheet("")
        self.refresh_counts()

    # ------------------------------------------------------------------
    # Step 2: review
    # ------------------------------------------------------------------
    def _build_review_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        header = PanelFrame()
        header_layout = QVBoxLayout(header)
        title = QLabel("REVIEW / CORRECT TRAINING DATA")
        title.setObjectName("PanelTitle")
        header_layout.addWidget(title)
        note = QLabel(
            "Review the persistent global terminal-top dataset before training. "
            "Correct a class label or remove a bad crop here; you do not need to browse folders. "
            "Collection targets are guidance only and never block training by themselves."
        )
        note.setWordWrap(True)
        note.setProperty("muted", True)
        header_layout.addWidget(note)
        self.review_scope = QLabel("Dataset scope will appear after samples are captured.")
        self.review_scope.setProperty("muted", True)
        header_layout.addWidget(self.review_scope)
        root.addWidget(header)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("CLASS"))
        self.review_class_filter = QComboBox()
        self.review_class_filter.addItem("ALL CLASSES", "")
        for label in REVIEW_LABELS:
            self.review_class_filter.addItem(self._label_text(label), label)
        self.review_class_filter.currentIndexChanged.connect(self._review_filter_changed)
        filters.addWidget(self.review_class_filter)
        filters.addSpacing(14)
        filters.addWidget(QLabel("BATTERY / TERMINAL FAMILY"))
        self.review_tag_filter = QComboBox()
        self.review_tag_filter.addItem("ALL FAMILIES", "")
        self.review_tag_filter.currentIndexChanged.connect(self._review_filter_changed)
        filters.addWidget(self.review_tag_filter, 1)
        self.review_refresh_button = QPushButton("REFRESH")
        self.review_refresh_button.clicked.connect(self._refresh_sample_browser)
        filters.addWidget(self.review_refresh_button)
        root.addLayout(filters)

        self._review_page_index = 0
        self._review_page_size = 6
        self._review_filtered_samples: list[dict[str, Any]] = []
        self.review_cards: list[dict[str, Any]] = []
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index in range(self._review_page_size):
            panel = PanelFrame(subpanel=True)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(4)
            preview = CropPreview()
            preview.setMinimumSize(180, 115)
            layout.addWidget(preview, 1)
            meta = QLabel("—")
            meta.setWordWrap(True)
            meta.setProperty("muted", True)
            layout.addWidget(meta)
            actions = QHBoxLayout()
            label_combo = QComboBox()
            for label in TRAINING_LABELS:
                label_combo.addItem(self._label_text(label), label)
            remove = QPushButton("REMOVE")
            remove.setObjectName("DangerButton")
            actions.addWidget(label_combo, 1)
            actions.addWidget(remove)
            layout.addLayout(actions)
            card = {
                "panel": panel,
                "preview": preview,
                "meta": meta,
                "label": label_combo,
                "remove": remove,
                "sample": None,
            }
            label_combo.currentIndexChanged.connect(
                lambda _value, card_index=index: self._review_relabel(card_index)
            )
            remove.clicked.connect(
                lambda _checked=False, card_index=index: self._review_remove(card_index)
            )
            self.review_cards.append(card)
            grid.addWidget(panel, index // 3, index % 3)
        root.addLayout(grid, 1)

        self.review_pager = PageNavigator("DATA PAGE")
        self.review_pager.previous_requested.connect(
            lambda: self._set_review_page(self._review_page_index - 1)
        )
        self.review_pager.next_requested.connect(
            lambda: self._set_review_page(self._review_page_index + 1)
        )
        root.addWidget(self.review_pager)

        self.review_status = QLabel()
        self.review_status.setWordWrap(True)
        root.addWidget(self.review_status)
        return page

    def _review_filter_changed(self, *_args) -> None:
        self._review_page_index = 0
        self._refresh_sample_browser()

    def _refresh_review_tag_filter(self, samples: list[dict[str, Any]]) -> None:
        current = str(self.review_tag_filter.currentData() or "")
        tags = sorted(
            {str(item.get("collection_tag", "")).strip() for item in samples if str(item.get("collection_tag", "")).strip()},
            key=str.casefold,
        )
        self.review_tag_filter.blockSignals(True)
        self.review_tag_filter.clear()
        self.review_tag_filter.addItem("ALL FAMILIES", "")
        for tag in tags:
            self.review_tag_filter.addItem(tag, tag)
        selected = self.review_tag_filter.findData(current)
        self.review_tag_filter.setCurrentIndex(selected if selected >= 0 else 0)
        self.review_tag_filter.blockSignals(False)

    def _set_review_page(self, index: int) -> None:
        self._review_page_index = max(0, int(index))
        self._render_review_page()

    def _refresh_sample_browser(self) -> None:
        if not hasattr(self, "review_cards"):
            return
        samples = list(self.controller.ml_training_samples())
        self._refresh_review_tag_filter(samples)
        class_filter = str(self.review_class_filter.currentData() or "")
        tag_filter = str(self.review_tag_filter.currentData() or "")
        self._review_filtered_samples = [
            item
            for item in samples
            if (not class_filter or str(item.get("label", "")) == class_filter)
            and (not tag_filter or str(item.get("collection_tag", "")) == tag_filter)
        ]
        self._render_review_page()

    def _render_review_page(self) -> None:
        import math

        total = len(self._review_filtered_samples)
        page_count = max(1, math.ceil(total / self._review_page_size))
        self._review_page_index = max(0, min(self._review_page_index, page_count - 1))
        start = self._review_page_index * self._review_page_size
        page_items = self._review_filtered_samples[start : start + self._review_page_size]
        for index, card in enumerate(self.review_cards):
            sample = page_items[index] if index < len(page_items) else None
            card["sample"] = sample
            panel = card["panel"]
            panel.setVisible(sample is not None)
            if sample is None:
                card["preview"].set_image(None)
                continue
            card["preview"].set_image(str(sample.get("image_path", "")))
            label = str(sample.get("label", ""))
            combo = card["label"]
            combo.blockSignals(True)
            selected = combo.findData(label)
            combo.setCurrentIndex(selected if selected >= 0 else 0)
            combo.blockSignals(False)
            capture_id = str(sample.get("source_capture_id", ""))
            family = str(sample.get("collection_tag", "")).strip() or "UNTAGGED"
            quality = dict(sample.get("crop_quality") or {})
            card["meta"].setText(
                f"{label.upper()}  •  {family}\n"
                f"Capture {capture_id[:14] or '—'}  •  "
                f"{int(sample.get('width_px', 0) or 0)} × {int(sample.get('height_px', 0) or 0)}  •  "
                f"Quality {str(quality.get('status', '—')).upper()}  •  "
                f"{str(sample.get('crop_contract', 'taught_circle_masked_square_v1'))}"
            )
        detail = f"{total} MATCHING / {len(self.controller.ml_training_samples())} TOTAL"
        self.review_pager.set_page(self._review_page_index, page_count if total else 0, detail)

    def _review_remove(self, card_index: int) -> None:
        if card_index < 0 or card_index >= len(self.review_cards):
            return
        sample = self.review_cards[card_index].get("sample")
        if not isinstance(sample, dict):
            return
        answer = QMessageBox.question(
            self,
            "Remove training image",
            f"Remove this {str(sample.get('label', '')).upper()} image from the persistent training dataset?\n\n"
            f"Family: {str(sample.get('collection_tag', '')).strip() or 'UNTAGGED'}\n"
            f"Capture: {str(sample.get('source_capture_id', ''))}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.controller.remove_ml_training_sample(str(sample.get("sample_id", ""))):
            self._refresh_sample_browser()
            self.refresh_counts()

    def _review_relabel(self, card_index: int) -> None:
        if card_index < 0 or card_index >= len(self.review_cards):
            return
        card = self.review_cards[card_index]
        sample = card.get("sample")
        if not isinstance(sample, dict):
            return
        new_label = str(card["label"].currentData() or "")
        old_label = str(sample.get("label", ""))
        if not new_label or new_label == old_label:
            return
        answer = QMessageBox.question(
            self,
            "Correct training label",
            f"Change this stored sample from {old_label.upper()} to {new_label.upper()}?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            card["label"].blockSignals(True)
            previous = card["label"].findData(old_label)
            card["label"].setCurrentIndex(previous if previous >= 0 else 0)
            card["label"].blockSignals(False)
            return
        try:
            self.controller.relabel_ml_training_sample(
                str(sample.get("sample_id", "")), new_label
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Training label not changed", str(exc))
            return
        self._refresh_sample_browser()
        self.refresh_counts()

    # ------------------------------------------------------------------
    # Step 3: prepare dataset and check runtime
    # ------------------------------------------------------------------
    def _build_prepare_page(self) -> QWidget:
        page = QWidget()
        root = QGridLayout(page)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)

        dataset_panel = PanelFrame()
        dataset_layout = QVBoxLayout(dataset_panel)
        title = QLabel("BUILD TRAIN / VALIDATION / TEST DATASET")
        title.setObjectName("PanelTitle")
        dataset_layout.addWidget(title)
        note = QLabel(
            "Samples captured from the same camera frame stay in the same split. This prevents the positive and negative crops from one image from leaking across training and held-out testing."
        )
        note.setWordWrap(True)
        note.setProperty("muted", True)
        dataset_layout.addWidget(note)
        form = QFormLayout()
        self.val_fraction = QDoubleSpinBox()
        self.val_fraction.setRange(0.05, 0.35)
        self.val_fraction.setSingleStep(0.05)
        self.val_fraction.setDecimals(2)
        self.val_fraction.setValue(0.15)
        self.test_fraction = QDoubleSpinBox()
        self.test_fraction.setRange(0.05, 0.35)
        self.test_fraction.setSingleStep(0.05)
        self.test_fraction.setDecimals(2)
        self.test_fraction.setValue(0.15)
        form.addRow("Validation fraction", self.val_fraction)
        form.addRow("Test fraction", self.test_fraction)
        dataset_layout.addLayout(form)
        self.prepare_dataset_button = QPushButton("PREPARE DATASET")
        self.prepare_dataset_button.setObjectName("PrimaryButton")
        self.prepare_dataset_button.clicked.connect(self.prepare_dataset)
        dataset_layout.addWidget(self.prepare_dataset_button)
        self.dataset_status = QLabel("Dataset has not been prepared in this session.")
        self.dataset_status.setWordWrap(True)
        dataset_layout.addWidget(self.dataset_status)
        self.dataset_split_values: dict[str, LabeledValue] = {}
        split_grid = QGridLayout()
        for row, split in enumerate(("train", "val", "test")):
            value = LabeledValue(split, "—")
            self.dataset_split_values[split] = value
            split_grid.addWidget(value, row, 0)
        dataset_layout.addLayout(split_grid)
        dataset_layout.addStretch(1)
        root.addWidget(dataset_panel, 0, 0)

        runtime_panel = PanelFrame()
        runtime_layout = QVBoxLayout(runtime_panel)
        runtime_title = QLabel("TRAINING RUNTIME")
        runtime_title.setObjectName("PanelTitle")
        runtime_layout.addWidget(runtime_title)
        runtime_note = QLabel(
            "Training uses Ultralytics/PyTorch only on the engineering workstation. The deployed inspection continues to use the exported ONNX model."
        )
        runtime_note.setWordWrap(True)
        runtime_note.setProperty("muted", True)
        runtime_layout.addWidget(runtime_note)
        self.runtime_state = LabeledValue("Training runtime", "NOT CHECKED")
        self.runtime_torch = LabeledValue("PyTorch", "—")
        self.runtime_ultralytics = LabeledValue("Ultralytics", "—")
        self.runtime_onnx = LabeledValue("ONNX Runtime", "—")
        self.runtime_gpu_hardware = LabeledValue("GPU hardware", "—")
        self.runtime_device = LabeledValue("PyTorch acceleration", "—")
        runtime_layout.addWidget(self.runtime_state)
        runtime_layout.addWidget(self.runtime_torch)
        runtime_layout.addWidget(self.runtime_ultralytics)
        runtime_layout.addWidget(self.runtime_onnx)
        runtime_layout.addWidget(self.runtime_gpu_hardware)
        runtime_layout.addWidget(self.runtime_device)
        self.runtime_check_button = QPushButton("CHECK TRAINING RUNTIME")
        self.runtime_check_button.clicked.connect(self.check_runtime)
        runtime_layout.addWidget(self.runtime_check_button)
        self.runtime_help = QLabel(
            "If components are missing, close the HMI and install requirements-training.txt into this same .venv. GPU/CUDA selection is intentionally not installed automatically."
        )
        self.runtime_help.setWordWrap(True)
        self.runtime_help.setProperty("muted", True)
        runtime_layout.addWidget(self.runtime_help)
        runtime_layout.addStretch(1)
        root.addWidget(runtime_panel, 0, 1)
        root.setColumnStretch(0, 3)
        root.setColumnStretch(1, 2)
        return page

    def prepare_dataset(self) -> None:
        # Collection targets are advisory. Always allow dataset preparation with
        # the samples currently available; the prepared summary reports whether
        # the data has the structural class/split coverage needed to train and
        # evaluate a four-class candidate.
        try:
            self.prepared_summary = self.controller.prepare_ml_training_dataset(
                validation_fraction=self.val_fraction.value(),
                test_fraction=self.test_fraction.value(),
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Dataset preparation failed", str(exc))
            return
        self._show_prepared_summary()

    def _show_prepared_summary(self) -> None:
        summary = self.prepared_summary
        if not summary:
            return
        counts = dict(summary.get("counts") or {})
        for split, widget in self.dataset_split_values.items():
            split_counts = dict(counts.get(split) or {})
            total = sum(int(value) for value in split_counts.values())
            abbreviations = {
                "plus": "P",
                "minus": "M",
                "blank": "B",
                "invalid_marking": "I",
            }
            detail = "  ".join(
                f"{abbreviations.get(label, label[:1].upper())}:"
                f"{int(split_counts.get(label, 0))}"
                for label in TRAINING_LABELS
            )
            widget.set_value(f"{total}  ({detail})", "good" if total else "warning")
        issues = list(summary.get("training_issues") or [])
        if issues:
            self.dataset_status.setText(
                "DATASET PREPARED — collection targets do not block training, but the current split has a structural issue: "
                + "; ".join(str(item) for item in issues)
            )
            self.dataset_status.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        else:
            advisory = " Held-out test coverage is partial." if not summary.get("held_out_class_coverage_complete", False) else ""
            self.dataset_status.setText(
                "DATASET PREPARED — ready for a candidate training run." + advisory + " Collection targets remain advisory."
            )
            self.dataset_status.setStyleSheet(f"color: {GOOD}; font-weight: 700;")

    def check_runtime(self) -> None:
        self.runtime_state.set_value("CHECKING…", "info")
        try:
            info = self.controller.ml_training_environment_info()
        except Exception as exc:  # noqa: BLE001
            self.runtime_state.set_value("CHECK FAILED", "bad")
            QMessageBox.critical(self, "Training runtime check failed", str(exc))
            return
        self.environment_info = info
        ready = bool(info.get("ready"))
        self.runtime_state.set_value("READY" if ready else "NOT READY", "good" if ready else "warning")
        self.runtime_torch.set_value(str(info.get("torch_version", "NOT INSTALLED") or "NOT INSTALLED"))
        self.runtime_ultralytics.set_value(str(info.get("ultralytics_version", "NOT INSTALLED") or "NOT INSTALLED"))
        self.runtime_onnx.set_value(str(info.get("onnxruntime_version", "NOT INSTALLED") or "NOT INSTALLED"))
        hardware = list(info.get("nvidia_hardware_names") or [])
        self.runtime_gpu_hardware.set_value(", ".join(hardware) if hardware else "NONE DETECTED")
        if info.get("cuda_available"):
            names = list(info.get("cuda_device_names") or [])
            self.runtime_device.set_value("CUDA — " + (", ".join(names) if names else "AVAILABLE"), "good")
        elif hardware:
            self.runtime_device.set_value("CPU-ONLY PYTORCH — CUDA NOT AVAILABLE", "warning")
        else:
            self.runtime_device.set_value("CPU", "neutral")
        warnings = list(info.get("warnings") or [])
        issues = list(info.get("issues") or [])
        if issues:
            self.runtime_help.setText("Runtime blockers:\n• " + "\n• ".join(str(item) for item in issues))
        elif warnings:
            self.runtime_help.setText("Runtime ready with warning:\n• " + "\n• ".join(str(item) for item in warnings))
        else:
            self.runtime_help.setText(
                "Training runtime and ONNX verification runtime are ready. GPU/CUDA package selection is intentionally not installed automatically."
            )
        self._populate_device_combo()

    # ------------------------------------------------------------------
    # Step 4: train
    # ------------------------------------------------------------------
    def _build_train_page(self) -> QWidget:
        page = QWidget()
        root = QHBoxLayout(page)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)

        config_panel = PanelFrame()
        config_layout = QVBoxLayout(config_panel)
        title = QLabel("TRAIN CANDIDATE MODEL")
        title.setObjectName("PanelTitle")
        config_layout.addWidget(title)
        note = QLabel(
            "Training runs in a background worker so the HMI remains responsive. Production inspection triggers are held while the training worker is active."
        )
        note.setWordWrap(True)
        note.setProperty("muted", True)
        config_layout.addWidget(note)
        form = QFormLayout()
        installed_base_model = (
            self.controller.data_directory
            / "models"
            / "training"
            / "yolo11n-cls.pt"
        )
        self.base_model = QLineEdit(
            str(installed_base_model)
            if is_frozen() or installed_base_model.is_file()
            else "yolo11n-cls.pt"
        )
        browse_row = QHBoxLayout()
        browse_row.addWidget(self.base_model, 1)
        browse_button = QPushButton("BROWSE")
        browse_button.clicked.connect(self.browse_base_model)
        browse_row.addWidget(browse_button)
        form.addRow("Base model", browse_row)
        self.epochs = QSpinBox()
        self.epochs.setRange(1, 1000)
        self.epochs.setValue(80)
        self.image_size = QComboBox()
        for value in (224, 256, 320):
            self.image_size.addItem(f"{value} x {value}", value)
        self.batch = QSpinBox()
        self.batch.setRange(1, 256)
        self.batch.setValue(32)
        self.training_device = QComboBox()
        self.training_device.addItem("AUTO — GPU IF AVAILABLE", "auto")
        self.model_id = QLineEdit("polarity-terminal-top-yolo")
        self.model_version = QLineEdit(datetime.now(timezone.utc).strftime("%Y.%m.%d.%H%M"))
        form.addRow("Epochs", self.epochs)
        form.addRow("Input size", self.image_size)
        form.addRow("Batch", self.batch)
        form.addRow("Device", self.training_device)
        form.addRow("Model ID", self.model_id)
        form.addRow("Model version", self.model_version)
        config_layout.addLayout(form)
        self.start_training_button = QPushButton("START MODEL TRAINING")
        self.start_training_button.setObjectName("PrimaryButton")
        self.start_training_button.clicked.connect(self.start_training)
        config_layout.addWidget(self.start_training_button)
        config_layout.addStretch(1)
        root.addWidget(config_panel, 2)

        progress_panel = PanelFrame()
        progress_layout = QVBoxLayout(progress_panel)
        progress_title = QLabel("TRAINING STATUS")
        progress_title.setObjectName("PanelTitle")
        progress_layout.addWidget(progress_title)
        self.training_progress = QProgressBar()
        self.training_progress.setRange(0, 100)
        self.training_progress.setValue(0)
        progress_layout.addWidget(self.training_progress)
        self.training_phase = LabeledValue("Phase", "WAITING")
        self.training_epoch = LabeledValue("Epoch", "—")
        self.training_message = QLabel(
            "Prepare the dataset and verify the training runtime before starting."
        )
        self.training_message.setWordWrap(True)
        self.training_message.setStyleSheet(
            f"background: {SURFACE_ALT}; border: 1px solid {BORDER}; padding: 10px; color: {TEXT_MUTED};"
        )
        progress_layout.addWidget(self.training_phase)
        progress_layout.addWidget(self.training_epoch)
        progress_layout.addWidget(self.training_message)
        self.training_output = LabeledValue("Candidate package", "—")
        progress_layout.addWidget(self.training_output)
        progress_layout.addStretch(1)
        root.addWidget(progress_panel, 3)
        return page

    def browse_base_model(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select Ultralytics classification base model",
            str(self.controller.data_directory / "models" / "training"),
            "PyTorch model (*.pt);;All files (*)",
        )
        if selected:
            self.base_model.setText(selected)

    def _populate_device_combo(self) -> None:
        current = str(self.training_device.currentData() or "auto")
        self.training_device.clear()
        self.training_device.addItem("AUTO — GPU IF AVAILABLE", "auto")
        self.training_device.addItem("CPU", "cpu")
        info = self.environment_info or {}
        if info.get("cuda_available"):
            names = list(info.get("cuda_device_names") or [])
            count = int(info.get("cuda_device_count", len(names)) or 0)
            for index in range(count):
                label = names[index] if index < len(names) else f"CUDA device {index}"
                self.training_device.addItem(f"CUDA {index} — {label}", str(index))
        index = self.training_device.findData(current)
        self.training_device.setCurrentIndex(index if index >= 0 else 0)

    def start_training(self) -> None:
        if self.prepared_summary is None:
            QMessageBox.warning(self, "Dataset not prepared", "Return to PREPARE and build the train/val/test dataset first.")
            return
        if self.environment_info is None or not self.environment_info.get("ready"):
            QMessageBox.warning(self, "Training runtime not ready", "Return to PREPARE and run CHECK TRAINING RUNTIME first.")
            return
        selected_device = str(self.training_device.currentData() or "auto")
        if selected_device == "auto":
            selected_device = (
                "0"
                if bool((self.environment_info or {}).get("cuda_available"))
                else "cpu"
            )
        parameters = MlTrainingParameters(
            base_model=self.base_model.text(),
            epochs=self.epochs.value(),
            image_size=int(self.image_size.currentData()),
            batch=self.batch.value(),
            device=selected_device,
            model_id=self.model_id.text(),
            model_version=self.model_version.text(),
        )
        self.training_progress.setValue(0)
        self.training_phase.set_value("STARTING", "info")
        self.training_message.setText("Starting background training worker…")
        try:
            started = self.controller.start_ml_training(parameters)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Training could not start", str(exc))
            return
        if not started:
            QMessageBox.information(
                self,
                "System occupied",
                "Training could not start because an inspection, recipe validation, or ML capture is still active.",
            )

    def _training_progress(self, payload: object) -> None:
        info = dict(payload)  # type: ignore[arg-type]
        stage = str(info.get("stage", "training")).upper()
        self.training_phase.set_value(stage, "info")
        self.training_progress.setValue(int(info.get("percent", self.training_progress.value()) or 0))
        epoch = info.get("epoch")
        epochs = info.get("epochs")
        if epoch is not None:
            self.training_epoch.set_value(f"{epoch} / {epochs}")
        self.training_message.setText(str(info.get("message", stage)))

    def _training_busy_changed(self, busy: bool) -> None:
        self._training_busy = bool(busy)
        self.start_training_button.setEnabled(not busy)
        self.capture_button.setEnabled(not busy and not self._capture_busy)
        self._update_save_batch_enabled()
        self.back_button.setEnabled(not busy and self._step > 0)
        self.next_button.setEnabled(not busy)
        if not busy and self.training_result is not None:
            self._refresh_deploy_status()

    def _training_completed(self, payload: object) -> None:
        self.training_result = dict(payload)  # type: ignore[arg-type]
        self.latest_candidate_button.setVisible(True)
        self.training_progress.setValue(100)
        self.training_phase.set_value("COMPLETE", "good")
        evaluation = dict(self.training_result.get("evaluation") or {})
        if evaluation.get("held_out_available", True):
            self.training_message.setText("Training, ONNX export, and held-out evaluation completed.")
        else:
            self.training_message.setText(
                "Training and ONNX export completed. No independent held-out test group was available; "
                "the candidate may still be installed for guided recipe validation after ONNX runtime verification."
            )
        self.training_output.set_value(str(self.training_result.get("run_directory", "")), "good")
        self._refresh_deploy_status()
        self.set_step(4)

    def _training_failed(self, message: str) -> None:
        self.training_phase.set_value("FAILED", "bad")
        recovered = self.controller.ml_training_latest_result()
        if recovered is not None:
            self.training_result = recovered
            self.latest_candidate_button.setVisible(True)
            self.training_message.setText(
                message
                + "\n\nAn exported candidate was recovered from this/another completed export. "
                "Repair the runtime if needed, then select LATEST CANDIDATE to verify/install it without retraining."
            )
        else:
            self.training_message.setText(message)
        QMessageBox.critical(self, "ML training failed", message)

    # ------------------------------------------------------------------
    # Step 5: evaluation/deploy
    # ------------------------------------------------------------------
    def _build_deploy_page(self) -> QWidget:
        page = QWidget()
        root = QGridLayout(page)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)

        metrics = PanelFrame()
        metrics_layout = QVBoxLayout(metrics)
        title = QLabel("HELD-OUT MODEL EVALUATION")
        title.setObjectName("PanelTitle")
        metrics_layout.addWidget(title)
        self.eval_total = LabeledValue("Test images", "—")
        self.eval_acceptance = LabeledValue("Acceptance rate", "—")
        self.eval_accuracy = LabeledValue("Accuracy with abstentions", "—")
        self.eval_accepted_accuracy = LabeledValue("Accepted-result accuracy", "—")
        for item in (self.eval_total, self.eval_acceptance, self.eval_accuracy, self.eval_accepted_accuracy):
            metrics_layout.addWidget(item)
        self.eval_class_values: dict[str, LabeledValue] = {}
        for label in TRAINING_LABELS:
            value = LabeledValue(f"{self._label_text(label)} recall", "—")
            self.eval_class_values[label] = value
            metrics_layout.addWidget(value)
        metrics_layout.addStretch(1)
        root.addWidget(metrics, 0, 0)

        deploy = PanelFrame()
        deploy_layout = QVBoxLayout(deploy)
        deploy_title = QLabel("INSTALL CANDIDATE ON STATION")
        deploy_title.setObjectName("PanelTitle")
        deploy_layout.addWidget(deploy_title)
        self.deploy_model = LabeledValue("Candidate", "—")
        self.deploy_hash = LabeledValue("SHA-256", "—")
        deploy_layout.addWidget(self.deploy_model)
        deploy_layout.addWidget(self.deploy_hash)
        self.deploy_policy = QCheckBox("Use this model for new and edited recipe revisions")
        self.deploy_policy.setChecked(True)
        deploy_layout.addWidget(self.deploy_policy)
        warning = QLabel(
            "Installing a candidate does not make an existing recipe production-ready. Every ML-bound recipe revision remains required to pass its guided recipe validation with the exact installed model SHA-256."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            f"color: {AMBER}; background: {AMBER_BG}; border: 1px solid {AMBER}; padding: 10px;"
        )
        deploy_layout.addWidget(warning)
        self.install_model_button = QPushButton("INSTALL CANDIDATE FOR RECIPE VALIDATION")
        self.install_model_button.setObjectName("PrimaryButton")
        self.install_model_button.clicked.connect(self.install_candidate)
        self.install_model_button.setEnabled(False)
        deploy_layout.addWidget(self.install_model_button)
        self.deploy_status = QLabel("No trained candidate is available in this session.")
        self.deploy_status.setWordWrap(True)
        deploy_layout.addWidget(self.deploy_status)
        self.new_round_button = QPushButton("RETURN TO CAPTURE MORE DATA")
        self.new_round_button.clicked.connect(lambda: self.set_step(0))
        deploy_layout.addWidget(self.new_round_button)
        deploy_layout.addStretch(1)
        root.addWidget(deploy, 0, 1)
        root.setColumnStretch(0, 3)
        root.setColumnStretch(1, 2)
        return page

    def _candidate_evaluation_warnings(self, evaluation: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        held_out_available = bool(
            evaluation.get(
                "held_out_available",
                int(evaluation.get("total_images", 0) or 0) > 0,
            )
        )
        per_class = dict(evaluation.get("per_class") or {})
        if not held_out_available:
            warnings.append("No independent held-out challenge set was evaluated.")
        missing = [
            self._label_text(label)
            for label in TRAINING_LABELS
            if int(dict(per_class.get(label) or {}).get("count", 0) or 0) <= 0
        ]
        if missing:
            warnings.append("Held-out set does not contain: " + ", ".join(missing) + ".")
        accepted_accuracy = float(evaluation.get("accepted_accuracy", 0.0) or 0.0)
        accuracy = float(evaluation.get("accuracy_with_abstentions", 0.0) or 0.0)
        if held_out_available and accepted_accuracy < 0.995:
            warnings.append(
                f"Accepted-result accuracy is {accepted_accuracy:.1%}; commissioning target is 99.5%."
            )
        if held_out_available and accuracy < 0.90:
            warnings.append(
                f"Accuracy with abstentions is {accuracy:.1%}; commissioning target is 90.0%."
            )
        return warnings

    def _refresh_deploy_status(self) -> None:
        result = self.training_result
        if not result:
            self.install_model_button.setEnabled(False)
            self.deploy_status.setText(
                "No exported candidate is available. Train a model or return after restoring the previous run artifacts."
            )
            self.deploy_status.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
            return
        evaluation = dict(result.get("evaluation") or {})
        self.eval_total.set_value(str(int(evaluation.get("total_images", 0) or 0)))
        self.eval_acceptance.set_value(
            f"{100.0 * float(evaluation.get('acceptance_rate', 0.0) or 0.0):.1f}%"
        )
        self.eval_accuracy.set_value(
            f"{100.0 * float(evaluation.get('accuracy_with_abstentions', 0.0) or 0.0):.1f}%"
        )
        self.eval_accepted_accuracy.set_value(
            f"{100.0 * float(evaluation.get('accepted_accuracy', 0.0) or 0.0):.1f}%"
        )
        per_class = dict(evaluation.get("per_class") or {})
        for label, widget in self.eval_class_values.items():
            details = dict(per_class.get(label) or {})
            recall = float(details.get("recall_with_abstentions", 0.0) or 0.0)
            count = int(details.get("count", 0) or 0)
            widget.set_value(f"{100.0 * recall:.1f}%  ({count} images)")
        self.deploy_model.set_value(str(result.get("model_path", "—")))
        digest = str(result.get("model_sha256", ""))
        self.deploy_hash.set_value(digest[:20] + "…" if digest else "—")

        package = self.controller.verify_ml_training_candidate(result)
        package_ready = bool(package.get("ready"))
        evaluation_warnings = self._candidate_evaluation_warnings(evaluation)
        self.install_model_button.setEnabled(package_ready and not self._training_busy)
        self.install_model_button.setText("INSTALL CANDIDATE FOR RECIPE VALIDATION")

        if not package_ready:
            issues = [str(item) for item in package.get("issues", [])]
            self.deploy_status.setText(
                "CANDIDATE PACKAGE NOT READY — installation is blocked until the ONNX model and runtime verify successfully.\n"
                + ("\n".join(issues) if issues else "Unknown ONNX package error.")
            )
            self.deploy_status.setStyleSheet(f"color: {BAD}; font-weight: 700;")
            return

        if evaluation_warnings:
            self.deploy_status.setText(
                "CANDIDATE PACKAGE VERIFIED — it may be installed for guided recipe validation. "
                "Held-out evaluation has warnings and is NOT being treated as a hard install gate:\n• "
                + "\n• ".join(evaluation_warnings)
                + "\nProduction activation still requires successful recipe validation with the exact installed model."
            )
            self.deploy_status.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        else:
            self.deploy_status.setText(
                "CANDIDATE PACKAGE VERIFIED AND COMMISSIONING METRICS MEET THE BUILT-IN TARGETS. "
                "Install it for guided recipe validation; recipe validation remains the production gate."
            )
            self.deploy_status.setStyleSheet(f"color: {GOOD}; font-weight: 700;")

    def install_candidate(self) -> None:
        if not self.training_result:
            return
        package = self.controller.verify_ml_training_candidate(self.training_result)
        if not bool(package.get("ready")):
            QMessageBox.critical(
                self,
                "Candidate package is not ready",
                "The ONNX package must pass runtime verification before installation.\n\n"
                + "\n".join(str(item) for item in package.get("issues", [])),
            )
            return
        evaluation = dict(self.training_result.get("evaluation") or {})
        warnings = self._candidate_evaluation_warnings(evaluation)
        if warnings:
            answer = QMessageBox.warning(
                self,
                "Install engineering candidate?",
                "This candidate is usable for recipe validation, but its held-out evaluation has warnings:\n\n• "
                + "\n• ".join(warnings)
                + "\n\nInstalling it does NOT approve it for production. Continue only to run guided recipe validation and collect more hard examples if needed.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            info = self.controller.install_ml_training_candidate(
                self.training_result,
                use_for_new_revisions=self.deploy_policy.isChecked(),
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Candidate could not be installed", str(exc))
            return
        self.deploy_status.setText(
            f"INSTALLED FOR RECIPE VALIDATION — {info.get('model_id', '')} {info.get('model_version', '')}. "
            "Existing recipe revisions are unchanged. Open RECIPES and choose EDIT / NEW REVISION so the new revision binds to this exact model, then run its guided validation."
        )
        self.deploy_status.setStyleSheet(f"color: {GOOD}; font-weight: 700;")
        QMessageBox.information(
            self,
            "ML candidate installed for validation",
            "The ONNX model is now the station candidate. Existing active recipe revisions are not modified.\n\n"
            "Next: open RECIPES → select the battery → EDIT / NEW REVISION. The new revision will bind to this model and must pass guided validation before activation.",
        )


    # ------------------------------------------------------------------
    # Common wizard navigation / summaries
    # ------------------------------------------------------------------
    def refresh_counts(self) -> None:
        summary = self.controller.ml_training_summary()
        counts = dict(summary.get("counts") or {})
        if hasattr(self, "capture_count_summary"):
            recommended = dict(summary.get("recommended") or {})
            parts = []
            for label in TRAINING_LABELS:
                count = int(counts.get(label, 0) or 0)
                target = int(recommended.get(label, 0) or 0)
                parts.append(f"{self._label_text(label)} {count}/{target}")
            self.capture_count_summary.setText(
                "DATASET — " + "   |   ".join(parts) + "   (targets advisory)"
            )
        if hasattr(self, "review_scope"):
            tag_count = int(summary.get("collection_tag_count", 0) or 0)
            capture_groups = int(summary.get("total_capture_groups", 0) or 0)
            total_samples = int(summary.get("total_samples", 0) or 0)
            stored_samples = int(summary.get("total_stored_samples", total_samples) or 0)
            tags = list(dict(summary.get("collection_tags") or {}).keys())
            tag_text = ", ".join(tags[:4]) if tags else "no family tags yet"
            if len(tags) > 4:
                tag_text += f" +{len(tags) - 4} more"
            self.review_scope.setText(
                f"CURRENT CIRCLE DATASET — {total_samples} eligible samples / {capture_groups} camera frames / "
                f"{tag_count} tagged battery or terminal families. Tags: {tag_text}. "
                f"Stored total: {stored_samples}. "
                "Input contract: taught circular terminal-face ROI / "
                "PLUS-MINUS-BLANK-INVALID MARKING."
            )
            missing_classes = [
                self._label_text(str(item))
                for item in summary.get("classes_without_samples", [])
            ]
            if missing_classes:
                self.review_status.setText(
                    "COLLECTION TARGETS ARE ADVISORY. Four-class training still needs at least "
                    "one labeled training example of: " + ", ".join(missing_classes) + "."
                )
                self.review_status.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
            else:
                below = [
                    self._label_text(str(item))
                    for item in summary.get("classes_below_target", [])
                ]
                suffix = (" Below target: " + ", ".join(below) + ".") if below else ""
                self.review_status.setText(
                    "CLASS COVERAGE PRESENT — review/remove bad crops as needed, then prepare a candidate whenever you are ready."
                    + suffix
                )
                self.review_status.setStyleSheet(f"color: {GOOD}; font-weight: 700;")
            self._refresh_sample_browser()

    def set_step(self, index: int) -> None:
        self._step = max(0, min(int(index), len(self.STEPS) - 1))
        self.stack.setCurrentIndex(self._step)
        self.steps.set_current_index(self._step)
        self.back_button.setEnabled(self._step > 0 and not self._training_busy)
        self.next_button.setVisible(self._step < len(self.STEPS) - 1)
        self.next_button.setEnabled(not self._training_busy)
        self.step_message.setText(f"STEP {self._step + 1} OF {len(self.STEPS)} — {self.STEPS[self._step].upper()}")
        if self._step in (0, 1):
            self.refresh_counts()
        if self._step == 2:
            self._show_prepared_summary()
        if self._step == 4:
            self._refresh_deploy_status()

    def go_back(self) -> None:
        if self._training_busy:
            return
        self.set_step(self._step - 1)

    def go_next(self) -> None:
        if self._training_busy:
            return
        if self._step == 2:
            if self.prepared_summary is None:
                QMessageBox.warning(self, "Dataset not prepared", "Select PREPARE DATASET before continuing to training.")
                return
            if self.environment_info is None or not self.environment_info.get("ready"):
                QMessageBox.warning(self, "Training runtime not ready", "Select CHECK TRAINING RUNTIME and resolve any missing training components.")
                return
        if self._step == 3 and self.training_result is None:
            QMessageBox.information(self, "Train a model first", "Complete model training before proceeding to deployment.")
            return
        self.set_step(self._step + 1)
