"""No page may be drawn smaller than the space its own contents need.

Qt does not overlap sibling widgets outright. When a layout runs short of room
it compresses its children below the size each one asked for, and a widget
given less height than its content needs -- a wrapped label above all -- paints
over whatever is beneath it. That is what an operator sees as overlapping, and
it is why the same build can look correct on one monitor and broken on another.

Monitor pixels are rarely the cause; the Windows scale factor is. A 3840x2160
panel at 150% offers a 1280x720 workspace, less height than this application's
own minimum window, so the most capable monitor on the plant floor is the one
most likely to show the fault.

These tests measure the deficit directly at several workspace sizes, on every
page and every step of the ML training page, with real samples on the review
grid so the thumbnail cards are actually laid out.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QLabel,
    QWidget,
)

from battery_inspector.evidence import reference_capture_from_file
from battery_inspector.models import NormalizedRect
from battery_inspector.ui import MainWindow

from conftest import ROOT

# The station's own minimum window, a common small panel, and two workspaces a
# scaled 4K or 1440p monitor actually reports.
WORKSPACES = [(1280, 760), (1366, 768), (1280, 720), (1024, 640)]


# Controls that arrange their own internals: a spin box's editor, a combo's
# line edit, a table's header. Those report sizes their owner never grants and
# never intends to, which is the control's business rather than the layout's.
# The control itself is measured like any other widget.
SELF_ARRANGING = (QAbstractSpinBox, QAbstractItemView, QComboBox)


def _is_control_internal(widget: QWidget) -> bool:
    return isinstance(widget.parentWidget(), SELF_ARRANGING)


def deficits(window: MainWindow) -> list[str]:
    problems: list[str] = []
    pending: list[QWidget] = [window]
    while pending:
        widget = pending.pop()
        pending.extend(
            child
            for child in widget.children()
            if isinstance(child, QWidget) and child.isVisible() and not child.isWindow()
        )
        if widget.isWindow() or _is_control_internal(widget):
            continue

        shortfall = widget.minimumSizeHint().height() - widget.height()
        if shortfall > 1:
            problems.append(
                f"{widget.__class__.__name__} is {shortfall}px shorter than it needs"
            )
            continue
        if isinstance(widget, QLabel) and widget.wordWrap() and widget.width() > 0:
            wrapped = widget.heightForWidth(widget.width()) - widget.height()
            if wrapped > 1:
                problems.append(
                    f"wrapped text is {wrapped}px too tall for its label: "
                    f"{widget.text()[:50]!r}"
                )
    return problems


def _seed_review_samples(controller) -> None:
    capture = reference_capture_from_file(
        ROOT / "battery_inspector" / "assets" / "demo_reference_good.png",
        source="TEST",
        camera_backend="bundled-asset",
        camera_description="Layout fixture",
    )
    for index, label in enumerate(("plus", "minus", "blank", "plus", "minus", "blank")):
        controller.ml_training_store.save_sample(
            capture,
            NormalizedRect(0.1 + 0.02 * index, 0.1, 0.14, 0.14),
            label,
            collection_tag="Group31 heavy duty long family name",
        )


@pytest.fixture()
def seeded_window(qapp, controller):
    _seed_review_samples(controller)
    window = MainWindow(controller)
    yield window
    window.close()
    qapp.processEvents()


@pytest.mark.parametrize("workspace", WORKSPACES)
def test_no_page_is_compressed_at_any_workspace_size(qapp, seeded_window, workspace) -> None:
    seeded_window.resize(*workspace)
    seeded_window.show()
    qapp.processEvents()

    for index in range(seeded_window.stack.count()):
        seeded_window.navigate(index)
        qapp.processEvents()
        page = seeded_window.current_page()
        found = deficits(seeded_window)
        assert not found, f"{page.__class__.__name__} at {workspace}: {found}"


@pytest.mark.parametrize("workspace", WORKSPACES)
def test_no_ml_training_step_is_compressed(qapp, seeded_window, workspace) -> None:
    """The capture and review steps are the ones an operator reported."""

    seeded_window.resize(*workspace)
    seeded_window.show()
    qapp.processEvents()
    seeded_window.navigate(MainWindow.ML_TRAINING)
    qapp.processEvents()
    page = seeded_window.current_page()

    for step in range(page.stack.count()):
        page.stack.setCurrentIndex(step)
        qapp.processEvents()
        found = deficits(seeded_window)
        assert not found, f"ML step {step} at {workspace}: {found}"
