from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.evidence import EvidenceError, export_evidence_package
from battery_inspector.models import (
    InspectionDisposition,
    InspectionResult,
    Marking,
    TerminalInspection,
    TerminalRecipe,
)
from battery_inspector.ui.image_widgets import CropPreview, ImageOverlayWidget, OverlaySpec
from battery_inspector.ui.palette import (
    ROLE_NEGATIVE,
    ROLE_POSITIVE,
    ROI_MARKING,
    TEXT_MUTED,
)
from battery_inspector.ui.widgets import (
    AMBER,
    BAD,
    GOOD,
    LabeledValue,
    PageNavigator,
    PanelFrame,
    ResultBadge,
)


class TerminalResultCard(PanelFrame):
    """Single-terminal evidence page.

    Terminal cards are displayed one at a time through a page navigator. This
    preserves large, obvious evidence without adding a vertical scrollbar.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._recipe: TerminalRecipe | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(7)

        self.header = QLabel("TERMINAL")
        self.header.setObjectName("PanelTitle")
        root.addWidget(self.header)

        content = QHBoxLayout()
        content.setSpacing(12)
        root.addLayout(content, 1)

        self.terminal_view = ImageOverlayWidget()
        self.terminal_view.setMinimumSize(310, 250)
        self.terminal_view.setMaximumWidth(470)
        content.addWidget(self.terminal_view, 3)

        detail_panel = QWidget()
        detail_layout = QGridLayout(detail_panel)
        detail_layout.setContentsMargins(5, 2, 5, 2)
        detail_layout.setHorizontalSpacing(20)
        detail_layout.setVerticalSpacing(5)
        self.expected = LabeledValue("Expected marking")
        self.detected = LabeledValue("Detected marking")
        self.confidence = LabeledValue("Classifier confidence")
        self.similarity = LabeledValue("Same-terminal reference")
        self.ring = LabeledValue("Red ring observation")
        self.ring_expected = LabeledValue("Red ring expected")
        self.analysis = LabeledValue("Analysis status")
        self.engine = LabeledValue("Classifier engine")
        self.geometry = LabeledValue("Stamp geometry")
        self.stamp_angle = LabeledValue("Stamp angle")
        self.top_lock = LabeledValue("Terminal face")
        self.finish_expected = LabeledValue("Expected finish")
        self.finish_detected = LabeledValue("Detected finish")
        self.result_badge = ResultBadge()
        detail_layout.addWidget(self.expected, 0, 0)
        detail_layout.addWidget(self.detected, 0, 1)
        detail_layout.addWidget(self.confidence, 1, 0)
        detail_layout.addWidget(self.ring, 1, 1)
        detail_layout.addWidget(self.similarity, 2, 0)
        detail_layout.addWidget(self.ring_expected, 2, 1)
        detail_layout.addWidget(self.geometry, 3, 0)
        detail_layout.addWidget(self.stamp_angle, 3, 1)
        detail_layout.addWidget(self.top_lock, 4, 0)
        detail_layout.addWidget(self.finish_expected, 4, 1)
        detail_layout.addWidget(self.finish_detected, 5, 0)
        detail_layout.addWidget(self.result_badge, 5, 1)
        detail_layout.addWidget(self.engine, 6, 0, 1, 2)
        detail_layout.addWidget(self.analysis, 7, 0, 1, 2)
        detail_layout.setRowStretch(8, 1)
        content.addWidget(detail_panel, 2)

        crop_panel = QWidget()
        crop_layout = QVBoxLayout(crop_panel)
        crop_layout.setContentsMargins(0, 0, 0, 0)
        crop_label = QLabel("MARKING CROP — EXACT SAVED EVIDENCE")
        crop_label.setObjectName("SectionTitle")
        crop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        crop_layout.addWidget(crop_label)
        crop_controls = QHBoxLayout()
        crop_controls.setSpacing(6)
        self.raw_crop_button = QPushButton("RAW MARKING")
        self.raw_crop_button.setCheckable(True)
        self.raw_crop_button.setChecked(True)
        self.raw_crop_button.clicked.connect(lambda: self._set_crop_mode(0))
        self.analysis_crop_button = QPushButton("INPUT VALIDITY")
        self.analysis_crop_button.setCheckable(True)
        self.analysis_crop_button.clicked.connect(lambda: self._set_crop_mode(1))
        self.analysis_crop_button.setEnabled(False)
        crop_controls.addWidget(self.raw_crop_button)
        crop_controls.addWidget(self.analysis_crop_button)
        crop_layout.addLayout(crop_controls)

        self.crop_stack = QStackedWidget()
        self.marking_crop = CropPreview()
        self.marking_crop.setMinimumSize(235, 210)
        self.stamp_analysis_crop = CropPreview()
        self.stamp_analysis_crop.setMinimumSize(235, 210)
        self.crop_stack.addWidget(self.marking_crop)
        self.crop_stack.addWidget(self.stamp_analysis_crop)
        crop_layout.addWidget(self.crop_stack, 1)
        note = QLabel("Raw crop is exact evidence; Input Validity shows the physical-input gate")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        crop_layout.addWidget(note)
        content.addWidget(crop_panel, 2)

    def _set_crop_mode(self, index: int) -> None:
        if index == 1 and not self.analysis_crop_button.isEnabled():
            index = 0
        self.crop_stack.setCurrentIndex(index)
        self.raw_crop_button.setChecked(index == 0)
        self.analysis_crop_button.setChecked(index == 1)

    def set_recipe(self, recipe: TerminalRecipe) -> None:
        self._recipe = recipe

    @staticmethod
    def _existing_path(value: str | None) -> Path | None:
        if not value:
            return None
        path = Path(value)
        return path if path.is_file() else None

    def set_result(self, result: TerminalInspection) -> None:
        role_color = ROLE_POSITIVE if result.role.value == "positive" else ROLE_NEGATIVE
        self.header.setText(f"{result.role.display} TERMINAL — {result.terminal_name}")
        self.header.setStyleSheet(
            f"color: {role_color}; font-weight: 800; font-size: 16px;"
        )
        terminal_path = self._existing_path(result.terminal_crop_path)
        marking_path = self._existing_path(result.marking_crop_path)
        terminal_available = result.terminal_crop_image is not None or terminal_path is not None
        if result.terminal_crop_image is not None:
            self.terminal_view.set_array(result.terminal_crop_image)
        else:
            self.terminal_view.set_image(terminal_path)
        if terminal_available and self._recipe is not None:
            self.terminal_view.set_overlays(
                [
                    OverlaySpec(
                        key="marking",
                        rect=self._recipe.marking_roi,
                        label=(
                            "MARKING CIRCLE"
                            if self._recipe.marking_roi_shape == "circle"
                            else "MARKING ROI"
                        ),
                        color=ROI_MARKING,
                        dashed=True,
                        line_width=3,
                        shape=self._recipe.marking_roi_shape,
                    )
                ]
            )
        else:
            self.terminal_view.set_overlays([])
        if result.marking_crop_image is not None:
            self.marking_crop.set_array(result.marking_crop_image)
        else:
            self.marking_crop.set_image(marking_path)
        face_overlay_path = self._existing_path(
            result.diagnostic_image_paths.get("terminal_face_overlay")
        )
        finish_compare_path = self._existing_path(
            result.diagnostic_image_paths.get("terminal_finish_compare")
        )
        stamp_overlay_path = self._existing_path(
            finish_compare_path
            or face_overlay_path
            or result.diagnostic_image_paths.get("stamp_overlay")
            or result.diagnostic_image_paths.get("terminal_top_overlay")
        )
        diagnostic_key = (
            "terminal_finish_compare"
            if "terminal_finish_compare" in result.diagnostic_images
            else (
                "terminal_face_overlay"
                if "terminal_face_overlay" in result.diagnostic_images
                else (
                    "stamp_overlay"
                    if "stamp_overlay" in result.diagnostic_images
                    else "terminal_top_overlay"
                )
            )
        )
        diagnostic_image = result.diagnostic_images.get(diagnostic_key)
        if diagnostic_image is not None:
            self.stamp_analysis_crop.set_array(diagnostic_image)
        else:
            self.stamp_analysis_crop.set_image(stamp_overlay_path)
        diagnostic_available = diagnostic_image is not None or stamp_overlay_path is not None
        self.analysis_crop_button.setEnabled(diagnostic_available)
        self.analysis_crop_button.setText(
            "FINISH CHECK"
            if finish_compare_path is not None or diagnostic_key == "terminal_finish_compare"
            else "INPUT VALIDITY"
            if face_overlay_path is not None or diagnostic_key == "terminal_face_overlay"
            else "STAMP ANALYSIS"
        )
        self._set_crop_mode(0)

        self.expected.set_value(result.expected_marking.display)
        ml_no_decision = (
            result.detected_marking == Marking.UNREADABLE
            and bool(result.classification_metrics.get("ml_model_id"))
        )
        if result.marking_evaluated:
            self.detected.set_value(
                "NO DECISION" if ml_no_decision else result.detected_marking.display,
                "good" if result.marking_pass else "bad",
            )
            self.confidence.set_value(f"{result.marking_confidence:.1%}")
            self.similarity.set_value(f"{result.reference_similarity:.1%}")
        else:
            self.detected.set_value("NOT EVALUATED", "warning")
            self.confidence.set_value("—", "warning")
            self.similarity.set_value("—", "warning")

        if result.ring_evaluated:
            self.ring.set_value(
                "YES" if result.red_ring_detected else "NO",
                "good" if result.ring_pass else "bad",
            )
        else:
            preview = "YES" if result.red_ring_detected else "NO"
            self.ring.set_value(f"PREVIEW ONLY: {preview}", "warning")
        self.ring_expected.set_value("YES" if result.red_ring_expected else "NO")
        self.finish_expected.set_value(result.expected_finish.display)
        if result.expected_finish.value == "unspecified":
            self.finish_detected.set_value("LEGACY — NOT CONFIGURED", "warning")
        elif result.finish_evaluated:
            self.finish_detected.set_value(
                f"{result.detected_finish.display} ({result.finish_confidence:.1%})",
                "good" if result.finish_pass else "bad",
            )
        else:
            self.finish_detected.set_value("NO DECISION", "bad")

        metrics = result.classification_metrics
        ml_mode = bool(metrics.get("ml_model_id"))
        if ml_mode:
            self.engine.set_value(
                "ML / ONNX — "
                f"{metrics.get('ml_model_id', '')} "
                f"{metrics.get('ml_model_version', '')}",
                "good" if result.marking_evaluated else "warning",
            )
            margin = metrics.get("ml_margin")
            self.similarity.set_value(
                f"ML margin {float(margin):.1%}"
                if isinstance(margin, (int, float))
                else "ML / ONNX"
            )
        else:
            self.engine.set_value(
                str(metrics.get("classifier_engine", result.classification_status) or "LEGACY")
                .replace("_", " ")
                .upper()
            )
        geometry_value = str(metrics.get("geometry_marking", "") or "").strip()
        if geometry_value:
            self.geometry.set_value(
                geometry_value.replace("_", " ").upper(),
                "good" if geometry_value == result.detected_marking.value else "warning",
            )
        else:
            self.geometry.set_value("—")
        angle = metrics.get("stamp_angle_deg")
        try:
            self.stamp_angle.set_value(f"{float(angle):.1f}°")
        except (TypeError, ValueError):
            self.stamp_angle.set_value("—")
        if result.terminal_face_evaluated:
            face_text = (
                f"PRESENT {result.terminal_face_confidence:.1%}"
                if result.terminal_face_present
                else (
                    "MISSING "
                    if result.terminal_face_status == "TERMINAL_FACE_MISSING"
                    else "INVALID "
                )
                + f"{result.terminal_face_confidence:.1%}"
            )
            self.top_lock.set_value(
                face_text,
                "good" if result.terminal_face_present else "bad",
            )
        else:
            self.top_lock.set_value("NOT EVALUATED", "warning")

        if result.terminal_face_evaluated and not result.terminal_face_present:
            self.result_badge.set_result(False)
            self.analysis.set_value(
                (result.analysis_note or "Physical terminal face is missing or invalid")
                + " | marking classifier bypassed",
                "bad",
            )
        elif result.marking_evaluated and result.ring_evaluated:
            self.result_badge.set_result(result.passed)
            self.analysis.set_value(
                (
                    (result.analysis_note or "Graded")
                    + (
                        " | "
                        + ", ".join(
                            f"{key.upper()} {value:.1%}"
                            for key, value in sorted(
                                result.class_scores.items(),
                                key=lambda item: item[1],
                                reverse=True,
                            )
                        )
                        if result.class_scores
                        else ""
                    )
                ),
                "good" if result.passed else "bad",
            )
        else:
            self.result_badge.set_result(None, "NOT EVALUATED")
            self.analysis.set_value(
                result.analysis_note or "Inspection engine is not ready",
                "warning",
            )


class InspectionDetailPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._inspection: InspectionResult | None = None
        self._terminal_index = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        back = QPushButton("←  BACK")
        back.clicked.connect(self.back_requested)
        header.addWidget(back)
        title = QLabel("INSPECTION DETAIL")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.summary = QLabel("WAITING FOR INSPECTION")
        self.summary.setStyleSheet(
            f"font-size: 18px; font-weight: 800; color: {TEXT_MUTED};"
        )
        header.addWidget(self.summary)
        root.addLayout(header)

        self.cards_stack = QStackedWidget()
        self.cards: list[TerminalResultCard] = []
        root.addWidget(self.cards_stack, 1)

        self.terminal_pager = PageNavigator("TERMINAL")
        self.terminal_pager.previous_requested.connect(self._previous_terminal)
        self.terminal_pager.next_requested.connect(self._next_terminal)
        root.addWidget(self.terminal_pager)

        footer = PanelFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 8, 14, 8)
        self.reason = QLabel("—")
        self.reason.setWordWrap(True)
        self.reason.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.meta = QLabel("—")
        self.meta.setProperty("muted", True)
        self.meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.open_evidence_button = QPushButton("OPEN EVIDENCE FOLDER")
        self.open_evidence_button.clicked.connect(self._open_evidence_folder)
        self.open_evidence_button.setEnabled(False)
        self.export_evidence_button = QPushButton("EXPORT INSPECTION ZIP")
        self.export_evidence_button.setProperty("accent", True)
        self.export_evidence_button.clicked.connect(self._export_evidence_zip)
        self.export_evidence_button.setEnabled(False)
        actions = QVBoxLayout()
        actions.setSpacing(6)
        actions.addWidget(self.open_evidence_button)
        actions.addWidget(self.export_evidence_button)

        footer_layout.addWidget(self.reason, 2)
        footer_layout.addLayout(actions)
        footer_layout.addWidget(self.meta, 3)
        root.addWidget(footer)

    def _clear_cards(self) -> None:
        while self.cards_stack.count():
            widget = self.cards_stack.widget(0)
            self.cards_stack.removeWidget(widget)
            widget.deleteLater()
        self.cards.clear()
        self._terminal_index = 0
        self._update_terminal_pager()

    def set_recipe(self, terminals: list[TerminalRecipe]) -> None:
        self._clear_cards()
        for terminal in terminals:
            card = TerminalResultCard()
            card.set_recipe(terminal)
            self.cards_stack.addWidget(card)
            self.cards.append(card)
        if self.cards:
            self.cards_stack.setCurrentIndex(0)
        self._update_terminal_pager()

    def _ensure_card_count(self, count: int) -> None:
        if len(self.cards) == count:
            return
        self._clear_cards()
        for _ in range(count):
            card = TerminalResultCard()
            self.cards_stack.addWidget(card)
            self.cards.append(card)
        if self.cards:
            self.cards_stack.setCurrentIndex(0)
        self._update_terminal_pager()

    def _previous_terminal(self) -> None:
        self._show_terminal(self._terminal_index - 1)

    def _next_terminal(self) -> None:
        self._show_terminal(self._terminal_index + 1)

    def _show_terminal(self, index: int) -> None:
        if not self.cards:
            self._terminal_index = 0
            self._update_terminal_pager()
            return
        self._terminal_index = max(0, min(index, len(self.cards) - 1))
        self.cards_stack.setCurrentIndex(self._terminal_index)
        self._update_terminal_pager()

    def _update_terminal_pager(self) -> None:
        detail = ""
        if self._inspection and 0 <= self._terminal_index < len(self._inspection.terminals):
            terminal = self._inspection.terminals[self._terminal_index]
            detail = terminal.role.display.upper()
        self.terminal_pager.set_page(self._terminal_index, len(self.cards), detail)
        self.terminal_pager.setVisible(len(self.cards) > 1)

    @staticmethod
    def _result_style(disposition: InspectionDisposition) -> tuple[str, str]:
        if disposition == InspectionDisposition.PASS:
            return GOOD, "PASS"
        if disposition == InspectionDisposition.REJECT:
            return BAD, "REJECT"
        if disposition in {
            InspectionDisposition.NOT_READY,
            InspectionDisposition.INDETERMINATE,
        }:
            return AMBER, disposition.display
        return BAD, "SYSTEM FAULT"

    @staticmethod
    def _format_timestamp(value: str) -> str:
        if not value:
            return "—"
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return stamp.astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        except ValueError:
            return value

    def set_inspection(self, result: InspectionResult) -> None:
        self._inspection = result
        evidence_directory = Path(result.evidence_directory) if result.evidence_directory else None
        evidence_available = bool(evidence_directory and evidence_directory.is_dir())
        self.open_evidence_button.setEnabled(evidence_available)
        self.export_evidence_button.setEnabled(evidence_available)
        self._ensure_card_count(len(result.terminals))
        for card, terminal_result in zip(self.cards, result.terminals, strict=False):
            card.set_result(terminal_result)

        tone, label = self._result_style(result.disposition)
        self.summary.setText(label)
        self.summary.setStyleSheet(
            f"font-size: 22px; font-weight: 800; color: {tone};"
        )
        self.reason.setText(result.reason)
        self.reason.setStyleSheet(
            f"font-size: 17px; font-weight: 700; color: {tone};"
        )
        readiness = ""
        if result.readiness_issues:
            readiness = "\nReadiness: " + " | ".join(result.readiness_issues)
        registration = ""
        if result.locator_metrics:
            metrics = result.locator_metrics
            registration = (
                "\nRegistration: "
                f"{metrics.get('detector', result.locator_status)} | "
                f"{metrics.get('inliers', '—')} inliers | "
                f"{float(metrics.get('median_reprojection_error_px', 0.0) or 0.0):.2f} px | "
                f"{float(metrics.get('rotation_deg', 0.0) or 0.0):.1f}°"
            )
        storage = (
            "\nStorage: MEMORY ONLY — production PASS not retained"
            if result.passed and not evidence_available
            else "\nStorage: RETAINED NON-PASS EVIDENCE"
            if evidence_available
            else "\nStorage: EVIDENCE UNAVAILABLE"
        )
        self.meta.setText(
            f"Recipe: {result.recipe_name}\n"
            f"Cycle: {result.cycle_id or '—'} | Frame: {result.frame_id or '—'}\n"
            f"Captured: {self._format_timestamp(result.captured_at_utc)} | "
            f"{result.frame_width} × {result.frame_height} × {result.frame_channels} | "
            f"{result.duration_ms} ms | Trigger: {result.trigger_source}"
            f"{registration}{readiness}{storage}"
        )
        self._show_terminal(0)

    def _open_evidence_folder(self) -> None:
        if self._inspection is None or not self._inspection.evidence_directory:
            return
        directory = Path(self._inspection.evidence_directory)
        if not directory.is_dir():
            QMessageBox.warning(
                self,
                "Evidence unavailable",
                f"The inspection evidence folder was not found:\n{directory}",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))

    def _export_evidence_zip(self) -> None:
        if self._inspection is None or not self._inspection.evidence_directory:
            return
        source = Path(self._inspection.evidence_directory)
        suggested = Path.home() / f"{self._inspection.cycle_id or source.name}.zip"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Export inspection evidence",
            str(suggested),
            "ZIP archives (*.zip)",
        )
        if not selected:
            return
        try:
            exported = export_evidence_package(source, Path(selected))
        except (EvidenceError, OSError) as exc:
            QMessageBox.critical(
                self,
                "Export failed",
                str(exc),
            )
            return
        QMessageBox.information(
            self,
            "Inspection evidence exported",
            f"Saved:\n{exported}",
        )
