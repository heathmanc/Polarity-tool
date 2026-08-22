from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.models import InspectionDisposition
from battery_inspector.ui.palette import (
    AMBER,
    AMBER_BG,
    BAD,
    BAD_BG,
    BLUE,
    BORDER,
    GOOD,
    GOOD_BG,
    NEUTRAL_BG,
    SURFACE_STRONG,
    TEXT,
    TEXT_DISABLED,
    TEXT_MUTED,
    tone_color,
)

# Re-export the state colors for existing UI modules.
MUTED = TEXT_MUTED


class PanelFrame(QFrame):
    def __init__(self, parent: QWidget | None = None, *, subpanel: bool = False) -> None:
        super().__init__(parent)
        self.setProperty("subpanel" if subpanel else "panel", True)
        self.setFrameShape(QFrame.Shape.NoFrame)


class MetricCard(QWidget):
    def __init__(self, caption: str, value: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(1)
        self.caption = QLabel(caption.upper())
        self.caption.setObjectName("MetricCaption")
        self.value = QLabel(value)
        self.value.setObjectName("MetricValue")
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        self.setMinimumWidth(95)

    def set_value(self, value: str, tone: str | None = None) -> None:
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {tone_color(tone)};")


class StatusPill(QFrame):
    """Machine-state banner.

    Normal operation remains neutral. Red and amber are reserved for conditions
    requiring attention, while a pass/healthy state uses only a restrained green
    outline and text.
    """

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(142)
        self.setMaximumHeight(54)
        self._title = QLabel(title)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet("font-size: 20px; font-weight: 800;")
        self._subtitle = QLabel(subtitle)
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setStyleSheet("font-size: 10px; font-weight: 700;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 5, 9, 5)
        layout.setSpacing(0)
        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)
        self.set_state("neutral")

    def set_state(self, state: str, title: str | None = None, subtitle: str | None = None) -> None:
        if title is not None:
            self._title.setText(title)
        if subtitle is not None:
            self._subtitle.setText(subtitle)
        colors = {
            "good": (GOOD, SURFACE_STRONG, GOOD),
            "bad": (BAD, BAD_BG, BAD),
            "warning": (AMBER, AMBER_BG, AMBER),
            "neutral": (TEXT, SURFACE_STRONG, BORDER),
            "info": (BLUE, SURFACE_STRONG, BLUE),
        }
        fg, bg, border = colors.get(state, colors["neutral"])
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 2px solid {border}; border-radius: 3px; }}"
            f"QLabel {{ color: {fg}; }}"
        )


class NavButton(QPushButton):
    def __init__(self, symbol: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(f"{symbol}\n{label.upper()}", parent)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setMinimumHeight(66)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class ResultBadge(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label = QLabel("—")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.addWidget(self.label)
        self.setMinimumSize(135, 54)
        self.set_result(None)

    def set_result(self, passed: bool | None, text: str | None = None) -> None:
        """Compatibility helper for simple PASS/FAIL callers."""

        if passed is None:
            self._apply_style(TEXT_MUTED, NEUTRAL_BG, text or "WAITING")
        elif passed:
            self._apply_style(GOOD, GOOD_BG, text or "PASS")
        else:
            self._apply_style(BAD, BAD_BG, text or "FAIL")

    def set_disposition(
        self,
        disposition: InspectionDisposition | None,
        text: str | None = None,
    ) -> None:
        """Show product outcomes separately from readiness and station faults."""

        styles = {
            InspectionDisposition.PASS: (GOOD, GOOD_BG, "PASS"),
            InspectionDisposition.REJECT: (BAD, BAD_BG, "REJECT"),
            InspectionDisposition.NOT_READY: (AMBER, AMBER_BG, "NOT READY"),
            InspectionDisposition.INDETERMINATE: (AMBER, AMBER_BG, "INDETERMINATE"),
            InspectionDisposition.SYSTEM_FAULT: (BAD, BAD_BG, "SYSTEM FAULT"),
        }
        color, background, default_text = styles.get(
            disposition,
            (TEXT_MUTED, NEUTRAL_BG, "WAITING"),
        )
        self._apply_style(color, background, text or default_text)

    def _apply_style(self, color: str, background: str, label: str) -> None:
        self.label.setText(label)
        self.label.setStyleSheet(f"color: {color}; font-size: 28px; font-weight: 800;")
        self.setStyleSheet(
            f"QFrame {{ background: {background}; border: 2px solid {color}; border-radius: 3px; }}"
        )


class LabeledValue(QWidget):
    def __init__(self, caption: str, value: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(2)
        self.caption = QLabel(caption.upper())
        self.caption.setProperty("muted", True)
        self.caption.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {TEXT_MUTED};")
        self.value = QLabel(value)
        self.value.setWordWrap(True)
        self.value.setStyleSheet(f"font-size: 15px; color: {TEXT};")
        layout.addWidget(self.caption)
        layout.addWidget(self.value)

    def set_value(self, value: str, tone: str | None = None) -> None:
        self.value.setText(value)
        self.value.setStyleSheet(f"font-size: 15px; color: {tone_color(tone)};")


class HealthItem(QWidget):
    clicked = Signal()

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 1, 10, 1)
        layout.setSpacing(7)
        self.dot = QLabel("●")
        self.name = QLabel(name.upper())
        self.name.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        self.value = QLabel("—")
        self.value.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {TEXT};")
        layout.addWidget(self.dot)
        layout.addWidget(self.name)
        layout.addWidget(self.value)
        self.set_state(False, "WAITING")

    def set_state(self, ok: bool, text: str) -> None:
        # Healthy equipment is deliberately neutral; faults are conspicuous red.
        color = TEXT if ok else BAD
        self.dot.setText("●" if ok else "◆")
        self.dot.setStyleSheet(f"color: {color}; font-size: 15px;")
        self.value.setText(text)
        self.value.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {color};")

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit()
        super().mousePressEvent(event)


class RecentResults(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._results: list[bool] = []
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_results(self, results: Iterable[bool]) -> None:
        self._results = list(results)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._results:
            return
        margin = 4
        gap = 7
        count = len(self._results)
        available = max(1, self.width() - margin * 2 - gap * (count - 1))
        size = min(30, max(18, available // count))
        y = (self.height() - size) // 2
        x = margin
        for passed in self._results:
            color = QColor(GOOD if passed else BAD)
            fill = QColor(GOOD_BG if passed else BAD_BG)
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(fill)
            painter.drawRoundedRect(x, y, size, size, 3, 3)
            painter.setPen(QPen(color, 2.2))
            if passed:
                painter.drawLine(
                    int(x + size * 0.27),
                    int(y + size * 0.52),
                    int(x + size * 0.44),
                    int(y + size * 0.70),
                )
                painter.drawLine(
                    int(x + size * 0.44),
                    int(y + size * 0.70),
                    int(x + size * 0.75),
                    int(y + size * 0.32),
                )
            else:
                painter.drawLine(
                    int(x + size * 0.31),
                    int(y + size * 0.31),
                    int(x + size * 0.69),
                    int(y + size * 0.69),
                )
                painter.drawLine(
                    int(x + size * 0.69),
                    int(y + size * 0.31),
                    int(x + size * 0.31),
                    int(y + size * 0.69),
                )
            x += size + gap


class StepIndicator(QWidget):
    def __init__(self, steps: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.steps = steps
        self.current_index = 0
        self.setMinimumHeight(50)

    def set_current_index(self, index: int) -> None:
        self.current_index = max(0, min(index, len(self.steps) - 1))
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.steps:
            return
        width_per = self.width() / len(self.steps)
        center_y = 17
        for index, step in enumerate(self.steps):
            x = width_per * index + width_per / 2
            if index < self.current_index:
                color = QColor(TEXT_MUTED)
            elif index == self.current_index:
                color = QColor(BLUE)
            else:
                color = QColor(TEXT_DISABLED)
            if index < len(self.steps) - 1:
                next_x = width_per * (index + 1) + width_per / 2
                painter.setPen(QPen(QColor(BORDER), 2))
                painter.drawLine(int(x + 12), center_y, int(next_x - 12), center_y)
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(color)
            painter.drawEllipse(int(x - 10), center_y - 10, 20, 20)
            painter.setPen(QColor(SURFACE_STRONG))
            painter.drawText(
                int(x - 10),
                center_y - 10,
                20,
                20,
                Qt.AlignmentFlag.AlignCenter,
                str(index + 1),
            )
            painter.setPen(color)
            painter.drawText(
                int(width_per * index),
                31,
                int(width_per),
                18,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                step.upper(),
            )


class PageNavigator(QWidget):
    """Compact previous/next control used instead of visible scroll bars."""

    previous_requested = Signal()
    next_requested = Signal()

    def __init__(self, noun: str = "PAGE", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._noun = noun.upper()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.previous_button = QPushButton("◀  PREVIOUS")
        self.next_button = QPushButton("NEXT  ▶")
        self.page_label = QLabel(f"{self._noun} 0 OF 0")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setStyleSheet(f"font-weight: 800; color: {TEXT_MUTED};")
        self.previous_button.clicked.connect(self.previous_requested)
        self.next_button.clicked.connect(self.next_requested)
        layout.addWidget(self.previous_button)
        layout.addWidget(self.page_label, 1)
        layout.addWidget(self.next_button)
        self.set_page(0, 0)

    def set_page(self, index: int, count: int, detail: str = "") -> None:
        if count <= 0:
            self.page_label.setText(f"{self._noun} 0 OF 0")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        safe_index = max(0, min(index, count - 1))
        suffix = f" — {detail}" if detail else ""
        self.page_label.setText(f"{self._noun} {safe_index + 1} OF {count}{suffix}")
        self.previous_button.setEnabled(safe_index > 0)
        self.next_button.setEnabled(safe_index < count - 1)
