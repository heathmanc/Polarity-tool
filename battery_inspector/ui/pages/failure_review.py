"""Walk up to the station and deal with what rejected.

Every non-PASS product cycle already wrote a row and an evidence folder. Until
this page existed the only way to look at any of it was a file browser on the
station PC, so in practice nobody did, and the rejects that would have told you
the recipe or the lighting had drifted aged out of the retention window unread.

The page is a work queue, not a report: each failure has a triage state, and
each of the four things a technician wants to do with one -- look at it, keep
it, teach the model from it, hand it to quality, get rid of it -- is one button
away from the row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.controller import AppController
from battery_inspector.data.repository import (
    REVIEW_NEW,
    REVIEW_REVIEWED,
    REVIEW_TRAINING,
)
from battery_inspector.models import InspectionResult, Marking
from battery_inspector.ui.image_widgets import CropPreview
from battery_inspector.ui.palette import AMBER, TEXT_MUTED
from battery_inspector.ui.widgets import PageNavigator, PanelFrame

# The four classes the production model is trained on. Offered with no
# selection made, because the technician's answer is the whole point.
TRAINING_LABELS = (
    (Marking.PLUS.value, "PLUS  (+)"),
    (Marking.MINUS.value, "MINUS  (−)"),
    (Marking.BLANK.value, "BLANK  (no stamp)"),
    (Marking.INVALID_MARKING.value, "INVALID MARKING"),
)

STATE_LABELS = {
    REVIEW_NEW: "NEW",
    REVIEW_REVIEWED: "REVIEWED",
    REVIEW_TRAINING: "SENT TO TRAINING",
}


class TrueLabelDialog(QDialog):
    """Ask what is actually stamped on each terminal of a rejected part.

    Nothing is preselected, and the model's answer is shown only as context.
    A rejected part is precisely the case where the classifier may have been
    wrong; defaulting to what it said would train it on its own mistakes and
    quietly entrench them.
    """

    def __init__(self, record: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add to ML training")
        self.setModal(True)
        self._combos: dict[str, QComboBox] = {}

        root = QVBoxLayout(self)
        heading = QLabel(
            "Choose what is actually stamped on each terminal of this part.\n"
            "Leave a terminal unset to leave it out."
        )
        heading.setWordWrap(True)
        root.addWidget(heading)

        warning = QLabel(
            "This part rejected, so the classifier may have read it wrongly. "
            "Label what you see on the battery, not what the station reported."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {AMBER}; font-weight: 700;")
        root.addWidget(warning)

        payload = dict(record.get("payload") or {})
        for terminal in payload.get("terminals", []):
            if not isinstance(terminal, dict):
                continue
            key = str(terminal.get("terminal_key", ""))
            if not key:
                continue
            combo = QComboBox()
            combo.addItem("— leave out —", "")
            for value, label in TRAINING_LABELS:
                combo.addItem(label, value)
            self._combos[key] = combo
            detected = str(terminal.get("detected_marking", "") or "?").upper()
            confidence = float(terminal.get("marking_confidence", 0.0) or 0.0)
            name = str(terminal.get("terminal_name", key))

            row = QHBoxLayout()
            # The crop that will be added, not a description of it. The label is
            # a judgement about an image, so the image has to be on screen: this
            # is the marking crop the classifier itself was given.
            preview = CropPreview()
            preview.setFixedSize(140, 140)
            preview.set_image(str(terminal.get("marking_crop_path", "") or ""))
            row.addWidget(preview)

            column = QVBoxLayout()
            heading = QLabel(f"{name}\nstation read: {detected} ({confidence:.0%})")
            heading.setWordWrap(True)
            column.addWidget(heading)
            column.addWidget(combo)
            column.addStretch(1)
            row.addLayout(column, 1)
            root.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def labels(self) -> dict[str, str]:
        return {
            key: str(combo.currentData() or "")
            for key, combo in self._combos.items()
            if str(combo.currentData() or "")
        }


class FailureReviewPage(QWidget):
    inspection_selected = Signal(object)

    PAGE_SIZE = 12

    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._failures: list[dict] = []
        self._page_index = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("FAILURE REVIEW")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        self.counts = QLabel("—")
        self.counts.setStyleSheet(f"color: {TEXT_MUTED}; font-weight: 700;")
        header.addWidget(self.counts)
        header.addStretch(1)
        refresh = QPushButton("REFRESH")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        root.addLayout(header)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.state_filter = QComboBox()
        self.state_filter.addItem("All states", "")
        for state, label in STATE_LABELS.items():
            self.state_filter.addItem(label, state)
        self.age_filter = QComboBox()
        for label, days in (
            ("Last 24 hours", 1),
            ("Last 7 days", 7),
            ("Last 30 days", 30),
            ("Everything retained", 0),
        ):
            self.age_filter.addItem(label, days)
        self.age_filter.setCurrentIndex(1)
        self.reason_filter = QLineEdit()
        self.reason_filter.setPlaceholderText("Reason contains…")
        for widget in (self.state_filter, self.age_filter):
            widget.currentIndexChanged.connect(self.refresh)
        self.reason_filter.returnPressed.connect(self.refresh)
        filters.addWidget(QLabel("Show"))
        filters.addWidget(self.state_filter)
        filters.addWidget(self.age_filter)
        filters.addWidget(self.reason_filter, 1)
        root.addLayout(filters)

        panel = PanelFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["TIME", "RECIPE", "REASON", "STATE", "KEEP"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.verticalHeader().setDefaultSectionSize(34)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemDoubleClicked.connect(lambda _item: self.open_selected())
        layout.addWidget(self.table, 1)
        self.pager = PageNavigator("FAILURE PAGE")
        self.pager.previous_requested.connect(lambda: self._set_page(self._page_index - 1))
        self.pager.next_requested.connect(lambda: self._set_page(self._page_index + 1))
        layout.addWidget(self.pager)
        root.addWidget(panel, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.open_button = QPushButton("OPEN")
        self.open_button.setObjectName("PrimaryButton")
        self.reviewed_button = QPushButton("MARK REVIEWED")
        self.keep_button = QPushButton("KEEP")
        self.release_button = QPushButton("RELEASE")
        self.training_button = QPushButton("ADD TO ML TRAINING…")
        self.export_button = QPushButton("EXPORT SELECTED…")
        self.clear_button = QPushButton("CLEAR SELECTED…")
        self.clear_button.setObjectName("DangerButton")
        for button in (
            self.open_button,
            self.reviewed_button,
            self.keep_button,
            self.release_button,
            self.training_button,
            self.export_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self.clear_button)
        root.addLayout(actions)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setProperty("muted", True)
        root.addWidget(self.status)

        self.open_button.clicked.connect(self.open_selected)
        self.reviewed_button.clicked.connect(self.mark_reviewed)
        self.keep_button.clicked.connect(lambda: self._set_keep(True))
        self.release_button.clicked.connect(lambda: self._set_keep(False))
        self.training_button.clicked.connect(self.add_to_training)
        self.export_button.clicked.connect(self.export_selected)
        self.clear_button.clicked.connect(self.clear_selected)
        controller.failures_changed.connect(self.refresh)
        self.refresh()

    # --- data ---------------------------------------------------------------

    def refresh(self) -> None:
        since = ""
        days = int(self.age_filter.currentData() or 0)
        if days:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            self._failures = self.controller.list_failures(
                review_state=str(self.state_filter.currentData() or ""),
                since_utc=since,
                reason_contains=self.reason_filter.text().strip(),
            )
        except Exception as exc:  # noqa: BLE001
            self._failures = []
            self.status.setText(f"Could not read retained failures: {exc}")
        counts = self.controller.failure_counts()
        self.counts.setText(
            f"{counts.get('total', 0)} retained  ·  "
            f"{counts.get(REVIEW_NEW, 0)} new  ·  "
            f"{counts.get(REVIEW_REVIEWED, 0)} reviewed  ·  "
            f"{counts.get(REVIEW_TRAINING, 0)} sent to training  ·  "
            f"{counts.get('kept', 0)} held from retention"
        )
        self._set_page(self._page_index)

    def _page_count(self) -> int:
        return max(1, (len(self._failures) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def _set_page(self, index: int) -> None:
        self._page_index = max(0, min(index, self._page_count() - 1))
        start = self._page_index * self.PAGE_SIZE
        page = self._failures[start : start + self.PAGE_SIZE]
        self.table.setRowCount(len(page))
        for row, record in enumerate(page):
            timestamp = str(record.get("timestamp_utc", ""))
            try:
                shown = datetime.fromisoformat(timestamp).astimezone().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                shown = timestamp
            values = [
                shown,
                str(record.get("recipe_name", "")),
                str(record.get("reason", "")),
                STATE_LABELS.get(str(record.get("review_state", "")), "NEW"),
                "HELD" if record.get("keep") else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3 and record.get("review_state") == REVIEW_NEW:
                    item.setForeground(Qt.GlobalColor.black)
                self.table.setItem(row, column, item)
        self.pager.set_page(
            self._page_index,
            self._page_count(),
            f"{len(self._failures)} MATCHING",
        )
        empty = not self._failures
        self.status.setText(
            "No retained failures match these filters. Production PASS cycles are "
            "memory-only and never appear here."
            if empty
            else ""
        )

    def selected_records(self) -> list[dict]:
        start = self._page_index * self.PAGE_SIZE
        page = self._failures[start : start + self.PAGE_SIZE]
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [page[row] for row in rows if 0 <= row < len(page)]

    # --- actions ------------------------------------------------------------

    def open_selected(self) -> None:
        records = self.selected_records()
        if not records:
            return
        record = records[0]
        try:
            result = InspectionResult.from_dict(dict(record.get("payload") or {}))
        except Exception as exc:  # noqa: BLE001
            # Reported in place rather than as a dialog: a damaged record is a
            # thing to notice, not an action to confirm, and the reviewer can
            # still open the evidence folder for it.
            self.status.setText(f"This record could not be read: {exc}")
            return
        self.inspection_selected.emit(result)

    def mark_reviewed(self) -> None:
        records = self.selected_records()
        if not records:
            return
        self.controller.mark_failures_reviewed(
            [str(item["inspection_id"]) for item in records]
        )

    def _set_keep(self, keep: bool) -> None:
        records = self.selected_records()
        if not records:
            return
        self.controller.set_failures_kept(
            [str(item["inspection_id"]) for item in records], keep
        )

    def add_to_training(self) -> None:
        records = self.selected_records()
        if not records:
            return
        if len(records) > 1:
            self.status.setText(
                "Each part is labelled individually, so add one failure at a time."
            )
            return
        record = records[0]
        dialog = self.build_label_dialog(record)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        labels = dialog.labels()
        if not labels:
            return
        try:
            result = self.controller.send_failure_to_training(record, labels)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not add training samples", str(exc))
            return
        QMessageBox.information(
            self,
            "Added to ML training",
            f"{result['added']} sample(s) added"
            + (f", {result['duplicates']} already present" if result["duplicates"] else "")
            + ".\n\nTraining a new model does not change any recipe: an ML-bound "
            "revision stays bound to the model it was validated against.",
        )

    def build_label_dialog(self, record: dict) -> TrueLabelDialog:
        """Seam so headless tests can drive labelling without a modal."""

        return TrueLabelDialog(record, self)

    def export_selected(self) -> None:
        records = self.selected_records() or self._failures
        if not records:
            return
        suggested = (
            self.controller.project_root
            / f"Pole_Position_Failures_{datetime.now():%Y%m%d_%H%M%S}.zip"
        )
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Export failures for review",
            str(suggested),
            "Pole Position failure export (*.zip)",
        )
        if not selected:
            return
        try:
            result = self.controller.export_failures(records, Path(selected))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        manifest = result.get("manifest", {})
        missing = list(manifest.get("evidence_missing", []))
        QMessageBox.information(
            self,
            "Failures exported",
            f"{manifest.get('record_count', 0)} record(s) written to:\n"
            f"{result.get('path', '')}"
            + (
                f"\n\n{len(missing)} record(s) had no evidence left on the station "
                "and are listed in the index without images."
                if missing
                else ""
            ),
        )

    def clear_selected(self) -> None:
        records = self.selected_records()
        if not records:
            # Deliberately not a dialog: an empty selection is a no-op, and a
            # modal for it would train people to dismiss dialogs on this page
            # without reading them. The destructive prompt below is the one that
            # must be read.
            self.status.setText(
                "Select the failures to clear. Clearing never acts on the whole "
                "list implicitly."
            )
            return
        held = [item for item in records if item.get("keep")]
        unexported = [item for item in records if not item.get("exported_at_utc")]
        message = (
            f"Permanently delete {len(records)} failure record(s) and their "
            "evidence images?\n\nThis cannot be undone."
        )
        if held:
            message += f"\n\n{len(held)} of them are held from retention."
        if unexported:
            message += f"\n{len(unexported)} of them have never been exported."
        if not self.confirm_clear(message):
            return
        try:
            summary = self.controller.clear_failures(records)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not clear failures", str(exc))
            return
        self.status.setText(
            f"Cleared {summary['rows_removed']} record(s) and {summary['removed']} "
            f"evidence folder(s), reclaiming "
            f"{summary['bytes_removed'] / 1024 / 1024:.1f} MB."
        )

    def confirm_clear(self, message: str) -> bool:
        """Seam so headless tests can exercise clearing without a modal."""

        answer = QMessageBox.warning(
            self,
            "Clear failures",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
