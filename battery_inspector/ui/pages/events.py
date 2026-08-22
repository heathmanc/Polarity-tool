from __future__ import annotations

from datetime import datetime
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.controller import AppController
from battery_inspector.ui.widgets import PageNavigator, PanelFrame


class EventsPage(QWidget):
    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._events: list[dict] = []
        self._page_index = 0
        self._page_size = 14
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("EVENTS & AUDIT TRAIL")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        refresh = QPushButton("REFRESH")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        root.addLayout(header)

        panel = PanelFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["TIME", "USER", "CATEGORY", "MESSAGE"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.pager = PageNavigator("EVENT PAGE")
        self.pager.previous_requested.connect(lambda: self._set_page(self._page_index - 1))
        self.pager.next_requested.connect(lambda: self._set_page(self._page_index + 1))
        layout.addWidget(self.pager)
        root.addWidget(panel, 1)
        self.refresh()

    def refresh(self) -> None:
        self._events = list(self.controller.audit_events())
        self._render_page()

    def _set_page(self, page_index: int) -> None:
        self._page_index = int(page_index)
        self._render_page()

    def _render_page(self) -> None:
        page_count = max(1, math.ceil(len(self._events) / self._page_size))
        self._page_index = max(0, min(self._page_index, page_count - 1))
        start = self._page_index * self._page_size
        end = min(len(self._events), start + self._page_size)
        visible = self._events[start:end]

        self.table.clearContents()
        self.table.setRowCount(len(visible))
        for row, event in enumerate(visible):
            timestamp = event["timestamp_utc"]
            try:
                display_time = datetime.fromisoformat(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                display_time = timestamp
            values = [display_time, event["username"], event["category"], event["message"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)
        self.pager.set_page(
            self._page_index,
            page_count,
            f"{len(self._events)} EVENTS",
        )
