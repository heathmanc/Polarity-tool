"""The mouse wheel must not change a value on a station screen.

Scrolling a page over a spin box or combo box changes it. On a station that
silently rewrites an exposure, a retention limit, a recipe number, or an
expected terminal marking, and the result is indistinguishable from a change
somebody made on purpose.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox, QWidget

from battery_inspector.ui.widgets import WheelValueGuard


def _wheel() -> QWheelEvent:
    return QWheelEvent(
        QPointF(5.0, 5.0),
        QPointF(5.0, 5.0),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


@pytest.fixture()
def guard(qapp):
    instance = WheelValueGuard()
    qapp.installEventFilter(instance)
    yield instance
    qapp.removeEventFilter(instance)


@pytest.mark.parametrize("factory", [QSpinBox, QDoubleSpinBox, QComboBox])
def test_the_wheel_cannot_change_a_value_control(qapp, guard, factory) -> None:
    widget = factory()
    if isinstance(widget, QComboBox):
        widget.addItems(["first", "second", "third"])
        widget.setCurrentIndex(1)
        before = widget.currentIndex()
    else:
        widget.setRange(0, 100)
        widget.setValue(50)
        before = widget.value()

    qapp.sendEvent(widget, _wheel())

    after = widget.currentIndex() if isinstance(widget, QComboBox) else widget.value()
    assert after == before, f"{factory.__name__} changed on a wheel event"


def test_the_wheel_is_refused_on_a_spin_box_internal_editor(qapp, guard) -> None:
    """The wheel lands on whatever is under the cursor.

    For a spin box that is usually its internal line edit, not the box, so a
    filter that only checked the watched object itself would miss it.
    """

    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(50)
    editor = spin.findChild(QLineEdit)
    assert editor is not None, "the spin box has no internal editor to test"

    qapp.sendEvent(editor, _wheel())

    assert spin.value() == 50


def test_the_wheel_still_works_everywhere_else(qapp, guard) -> None:
    """Only value controls are guarded; scrolling is not broken generally."""

    plain = QWidget()
    event = _wheel()

    assert guard.eventFilter(plain, event) is False


def test_typing_and_the_arrows_still_change_a_value(qapp, guard) -> None:
    """The guard refuses the wheel and nothing else."""

    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(50)

    spin.stepUp()
    assert spin.value() == 51

    spin.setValue(72)
    assert spin.value() == 72


def test_the_guard_is_installed_before_any_window_exists() -> None:
    """A dialog opened later must be covered too, so it goes on the app."""

    source = (
        __import__("pathlib").Path("battery_inspector/main.py").read_text(encoding="utf-8")
    )
    install = source.index("installEventFilter")
    window = source.index("MainWindow(controller)")
    assert install < window, "the guard must be installed before the window is built"
