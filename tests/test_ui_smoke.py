"""Headless construction and signal-delivery tests for the HMI.

These complement `test_hmi_style.py`, which asserts on page *source text*.
Source-text assertions cannot catch constructor drift, a renamed slot, or a
signal payload that no longer matches what the receiving page reads -- the HMI
still imports cleanly and every string assertion still passes while the window
fails to build at runtime. The tests here build the real widget tree against a
real controller and push real controller payloads through it.

They deliberately assert on displayed values rather than internal state: what
the operator is shown is the contract the ISA-101 style guide describes.
"""

from __future__ import annotations

import pytest

from battery_inspector.models import (
    InspectionCycleState,
    InspectionCycleStatus,
    Marking,
    NormalizedRect,
    Recipe,
    TerminalRecipe,
    TerminalRole,
)
from battery_inspector.ui import MainWindow
from battery_inspector.ui.pages.diagnostics import DiagnosticsPage
from battery_inspector.ui.pages.events import EventsPage
from battery_inspector.ui.pages.inspection_detail import InspectionDetailPage
from battery_inspector.ui.pages.ml_training import MlTrainingPage
from battery_inspector.ui.pages.overview import OverviewPage
from battery_inspector.ui.pages.recipes import RecipesPage
from battery_inspector.ui.pages.settings import SettingsPage
from battery_inspector.ui.wizard import RecipeWizardDialog

from conftest import drain

PAGE_TYPES = [
    (MainWindow.OVERVIEW, OverviewPage),
    (MainWindow.INSPECTION, InspectionDetailPage),
    (MainWindow.RECIPES, RecipesPage),
    (MainWindow.ML_TRAINING, MlTrainingPage),
    (MainWindow.DIAGNOSTICS, DiagnosticsPage),
    (MainWindow.EVENTS, EventsPage),
    (MainWindow.SETTINGS, SettingsPage),
]


@pytest.fixture()
def window(qapp, controller):
    instance = MainWindow(controller)
    yield instance
    instance.close()
    qapp.processEvents()


def _recipe(number: int = 7, name: str = "MODEL_A") -> Recipe:
    return Recipe.new(
        name=name,
        recipe_number=number,
        part_number="PN-1",
        description="UI smoke fixture",
        created_by="test",
        battery_roi=NormalizedRect(0.0, 0.0, 1.0, 1.0),
        terminals=[
            TerminalRecipe(
                key="negative",
                name="Negative Terminal",
                role=TerminalRole.NEGATIVE,
                search_roi=NormalizedRect(0.0, 0.0, 0.5, 1.0),
                marking_roi=NormalizedRect(0.1, 0.1, 0.3, 0.3),
                expected_marking=Marking.MINUS,
                red_ring_required=False,
            ),
            TerminalRecipe(
                key="positive",
                name="Positive Terminal",
                role=TerminalRole.POSITIVE,
                search_roi=NormalizedRect(0.5, 0.0, 0.5, 1.0),
                marking_roi=NormalizedRect(0.6, 0.1, 0.3, 0.3),
                expected_marking=Marking.PLUS,
                red_ring_required=True,
            ),
        ],
    )


def test_main_window_builds_every_page(window) -> None:
    assert window.stack.count() == len(PAGE_TYPES)
    for index, page_type in PAGE_TYPES:
        assert isinstance(window.stack.widget(index), page_type)


@pytest.mark.parametrize("index,page_type", PAGE_TYPES)
def test_every_page_can_be_navigated_to(window, index, page_type) -> None:
    window.navigate(index)

    assert window.stack.currentIndex() == index
    assert isinstance(window.stack.currentWidget(), page_type)


def test_window_opens_on_overview_with_the_controller_baseline(window, controller) -> None:
    assert window.stack.currentIndex() == MainWindow.OVERVIEW
    assert window.part_metric.value.text() == "0"
    assert window.pass_metric.value.text() == "0"
    assert window.fail_metric.value.text() == "0"
    assert controller.part_count == 0


def test_counts_signal_reaches_the_header_metrics(qapp, window, controller) -> None:
    controller.part_count = 20
    controller.pass_count = 17
    controller.fail_count = 3
    controller.recent_results.extend([True, False, True])

    controller.counts_changed.emit(controller.counts_payload())
    qapp.processEvents()

    assert window.part_metric.value.text() == "20"
    assert window.pass_metric.value.text() == "17"
    assert window.fail_metric.value.text() == "3"
    assert window.reject_metric.value.text() == "15.0%"


def test_health_signal_reaches_the_header_and_diagnostics(qapp, window, controller) -> None:
    controller.health["camera"] = {"ok": True, "text": "SIMULATION"}
    controller.health["plc"] = {"ok": False, "text": "FAULT"}

    controller.health_changed.emit(controller.health)
    qapp.processEvents()

    assert window.health_items["camera"].value.text() == "SIMULATION"
    assert window.health_items["plc"].value.text() == "FAULT"


def test_active_recipe_signal_reaches_the_header(qapp, window, controller) -> None:
    recipe = _recipe(number=7, name="MODEL_A")

    controller.active_recipe_changed.emit(recipe)
    qapp.processEvents()

    assert window.active_recipe_metric.value.text() == "7 — MODEL_A"


def test_cycle_state_signal_is_accepted_by_the_window(qapp, window, controller) -> None:
    status = InspectionCycleStatus.idle()

    controller.cycle_state_changed.emit(status)
    qapp.processEvents()

    assert window._cycle_status.state is InspectionCycleState.IDLE


def test_configuration_signal_updates_the_operator_labels(qapp, window, controller) -> None:
    import dataclasses

    updated = dataclasses.replace(controller.config, operator_name="A. Technician")

    controller.configuration_changed.emit(updated)
    qapp.processEvents()

    assert window.user_label.text() == "A. Technician"
    assert "A. Technician" in window.footer_user.text()


def test_window_reflects_simulation_backends_after_startup(qapp, window, controller) -> None:
    controller.initialize()
    drain(qapp)

    assert window.health_items["camera"].value.text() == "SIMULATION"
    assert window.health_items["plc"].value.text() == "SIMULATION"


# --- recipe wizard ---------------------------------------------------------
#
# The wizard is the largest single UI module and is only reachable through a
# modal dialog, so nothing previously constructed it. These tests build it and
# walk every step's prepare() without entering the modal event loop.


@pytest.fixture()
def wizard(qapp, controller):
    dialog = RecipeWizardDialog(
        controller=controller,
        username="test",
        initial_reference_action="choose",
    )
    yield dialog
    dialog.reject()
    qapp.processEvents()


def test_recipe_wizard_builds_all_seven_steps(wizard) -> None:
    assert RecipeWizardDialog.STEPS == [
        "Reference",
        "Identify",
        "Battery",
        "Terminals",
        "Polarity",
        "Validate",
        "Complete",
    ]
    assert len(wizard.pages) == len(RecipeWizardDialog.STEPS)
    assert wizard.stack.count() == len(RecipeWizardDialog.STEPS)


def test_recipe_wizard_opens_on_the_reference_step(wizard) -> None:
    assert wizard.stack.currentIndex() == 0
    assert wizard.back_button.isEnabled() is False
    assert wizard.next_button.text() == "NEXT  →"


def test_every_wizard_step_can_be_prepared(qapp, wizard) -> None:
    for index in range(len(wizard.pages)):
        wizard._show_page(index)
        qapp.processEvents()

        assert wizard.stack.currentIndex() == index

    # The final step commits, so its primary action stops saying NEXT.
    assert wizard.next_button.text() in {"SAVE DRAFT", "SAVE & ACTIVATE"}


def test_wizard_back_navigation_returns_to_the_first_step(qapp, wizard) -> None:
    wizard._show_page(2)
    qapp.processEvents()

    wizard.go_back()
    wizard.go_back()
    qapp.processEvents()

    assert wizard.stack.currentIndex() == 0
    assert wizard.back_button.isEnabled() is False


def test_validate_step_shows_the_blocker_when_no_reference_is_accepted(qapp, wizard) -> None:
    """Regression: the Validate step used to crash instead of listing blockers.

    ReadinessPage.prepare() catches the "accept a reference first" guard into
    its issue list, but then dereferenced the temporary recipe that the guard
    had prevented it from building, raising UnboundLocalError from inside the
    handler meant to keep the step usable.
    """

    validate_step = RecipeWizardDialog.STEPS.index("Validate")

    wizard._show_page(validate_step)
    qapp.processEvents()

    assert wizard.stack.currentIndex() == validate_step
    assert "reference" in wizard.pages[validate_step].gates.text().lower()
