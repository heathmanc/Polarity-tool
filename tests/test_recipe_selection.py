"""The PLC names the product on every trigger.

The station used to hold one active recipe, chosen by a technician, and a PLC
trigger naming anything else was ignored. That makes a mixed line impossible
without somebody standing at the HMI, and it makes headless operation
impossible outright.

Now the controller names a product per trigger and the station resolves it to
the newest revision of that recipe whose validation is complete. What is
resolved is what grades the part. Nothing else does.
"""

from __future__ import annotations

import dataclasses

import pytest

from battery_inspector.config import AppConfig, PlcTagMap
from battery_inspector.controller import AppController
from battery_inspector.data import RecipeRepository
from battery_inspector.models import (
    Marking,
    NormalizedRect,
    Recipe,
    RecipeStatus,
    TerminalFinish,
    TerminalRecipe,
    TerminalRole,
)

from conftest import ROOT, drain, mark_validated

GOOD_REFERENCE = ROOT / "battery_inspector" / "assets" / "demo_reference_good.png"


def _terminals() -> list[TerminalRecipe]:
    return [
        TerminalRecipe(
            key="negative",
            name="Negative Terminal",
            role=TerminalRole.NEGATIVE,
            search_roi=NormalizedRect(0.1, 0.1, 0.2, 0.2),
            marking_roi=NormalizedRect(0.3, 0.3, 0.3, 0.3),
            expected_marking=Marking.MINUS,
            red_ring_required=False,
            expected_finish=TerminalFinish.SILVER,
        ),
        TerminalRecipe(
            key="positive",
            name="Positive Terminal",
            role=TerminalRole.POSITIVE,
            search_roi=NormalizedRect(0.6, 0.6, 0.2, 0.2),
            marking_roi=NormalizedRect(0.3, 0.3, 0.3, 0.3),
            expected_marking=Marking.PLUS,
            red_ring_required=False,
            expected_finish=TerminalFinish.SILVER,
        ),
    ]


def _recipe(number: int, name: str, *, validated: bool, revision: int = 1) -> Recipe:
    recipe = Recipe.new(
        name=name,
        recipe_number=number,
        part_number=f"PN-{number}",
        description="",
        created_by="test",
        battery_roi=NormalizedRect(0.0, 0.0, 1.0, 1.0),
        terminals=_terminals(),
    )
    recipe.revision = revision
    if validated:
        mark_validated(recipe)
    return recipe


@pytest.fixture()
def repository(tmp_path):
    return RecipeRepository(tmp_path / "recipes.db")


# --- resolution -------------------------------------------------------------


def test_a_validated_recipe_resolves_by_number(repository) -> None:
    repository.save_recipe(_recipe(7, "GROUP31", validated=True), username="test")

    resolved = repository.resolve_production_recipe(recipe_number=7)

    assert resolved is not None
    assert resolved.name == "GROUP31"


def test_a_validated_recipe_resolves_by_name(repository) -> None:
    repository.save_recipe(_recipe(7, "GROUP31", validated=True), username="test")

    resolved = repository.resolve_production_recipe(recipe_name="GROUP31")

    assert resolved is not None
    assert resolved.recipe_number == 7


def test_an_unvalidated_recipe_never_resolves(repository) -> None:
    """The gate that matters is unchanged: validation, not a status word."""

    repository.save_recipe(_recipe(7, "GROUP31", validated=False), username="test")

    assert repository.resolve_production_recipe(recipe_number=7) is None


def test_an_unknown_number_resolves_to_nothing(repository) -> None:
    repository.save_recipe(_recipe(7, "GROUP31", validated=True), username="test")

    assert repository.resolve_production_recipe(recipe_number=99) is None


def test_the_newest_validated_revision_wins(repository) -> None:
    repository.save_recipe(_recipe(7, "GROUP31", validated=True, revision=1), username="t")
    newer = _recipe(7, "GROUP31", validated=True, revision=2)
    newer.part_number = "PN-NEWER"
    repository.save_recipe(newer, username="t")

    resolved = repository.resolve_production_recipe(recipe_number=7)

    assert resolved is not None
    assert resolved.revision == 2
    assert resolved.part_number == "PN-NEWER"


def test_a_newer_unvalidated_revision_does_not_displace_a_validated_one(repository) -> None:
    """A draft in progress must never take a validated revision out of service."""

    repository.save_recipe(_recipe(7, "GROUP31", validated=True, revision=1), username="t")
    repository.save_recipe(_recipe(7, "GROUP31", validated=False, revision=2), username="t")

    resolved = repository.resolve_production_recipe(recipe_number=7)

    assert resolved is not None
    assert resolved.revision == 1


def test_a_retired_revision_is_skipped(repository) -> None:
    retired = _recipe(7, "GROUP31", validated=True, revision=2)
    retired.status = RecipeStatus.RETIRED
    repository.save_recipe(_recipe(7, "GROUP31", validated=True, revision=1), username="t")
    repository.save_recipe(retired, username="t")

    resolved = repository.resolve_production_recipe(recipe_number=7)

    assert resolved is not None
    assert resolved.revision == 1


def test_many_products_are_runnable_at_once(repository) -> None:
    """The point of the change: a mixed line, with nobody at the HMI."""

    for number in (1, 2, 3):
        repository.save_recipe(_recipe(number, f"P{number}", validated=True), username="t")

    assert repository.production_recipe_count() == 3
    for number in (1, 2, 3):
        assert repository.resolve_production_recipe(recipe_number=number) is not None


def test_an_empty_selector_resolves_to_nothing(repository) -> None:
    repository.save_recipe(_recipe(7, "GROUP31", validated=True), username="test")

    assert repository.resolve_production_recipe(recipe_number=0) is None
    assert repository.resolve_production_recipe(recipe_name="  ") is None


# --- the station ------------------------------------------------------------


def _station(tmp_path, qapp, *, selector: str = "number"):
    root = tmp_path / "station"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    repository = RecipeRepository(runtime / "battery_inspector.db")
    repository.seed_demo_data(GOOD_REFERENCE)
    config = dataclasses.replace(
        AppConfig(),
        camera_backend="simulation",
        plc_backend="simulation",
        data_directory=str(runtime),
        plc_recipe_selector=selector,
        tags=PlcTagMap(ready="BatteryVision.Ready"),
    )
    instance = AppController(root, config, resource_root=ROOT)
    instance.initialize()
    drain(qapp)
    return instance


@pytest.fixture()
def station(qapp, tmp_path):
    instance = _station(tmp_path, qapp)
    yield instance
    instance.shutdown()
    qapp.processEvents()


def test_a_plc_cycle_grades_against_the_requested_recipe(qapp, station) -> None:
    seeded = station.repository.get_active_recipe()
    assert seeded is not None
    station.plc.recipe_selector = "number"
    station.plc.recipe_number = seeded.recipe_number
    station._handle_plc_state(station.plc.read_cycle_state())

    resolved = station.resolve_recipe_for_trigger("PLC")

    assert resolved is not None
    assert resolved.recipe_number == seeded.recipe_number


def test_a_plc_request_for_an_unrunnable_product_is_refused(qapp, station) -> None:
    """No result at all, and the trigger is not consumed by some other recipe."""

    station.plc.recipe_selector = "number"
    station.plc.recipe_number = 4242
    station._handle_plc_state(station.plc.read_cycle_state())

    assert station.resolve_recipe_for_trigger("PLC") is None
    assert station.run_inspection("PLC") is False


def test_readiness_drops_while_an_unrunnable_product_is_requested(qapp, station) -> None:
    station.plc.recipe_selector = "number"
    station.plc.recipe_number = 4242
    station._handle_plc_state(station.plc.read_cycle_state())
    drain(qapp)

    assert station.station_ready_for_trigger() is False
    assert station.plc.snapshot()["ready"] is False


def test_readiness_returns_when_a_runnable_product_is_requested(qapp, station) -> None:
    seeded = station.repository.get_active_recipe()
    assert seeded is not None
    station.plc.recipe_selector = "number"
    station.plc.recipe_number = 4242
    station._handle_plc_state(station.plc.read_cycle_state())
    drain(qapp)
    assert station.plc.snapshot()["ready"] is False

    station.plc.recipe_number = seeded.recipe_number
    station._handle_plc_state(station.plc.read_cycle_state())
    drain(qapp)

    assert station.plc.snapshot()["ready"] is True


def test_a_manual_trigger_still_uses_the_station_selection(qapp, station) -> None:
    """A technician at the HMI, and every simulation without a PLC."""

    selected = station.active_recipe
    assert selected is not None

    assert station.resolve_recipe_for_trigger("MANUAL") is selected


def test_no_selector_configured_falls_back_to_the_station_selection(qapp, station) -> None:
    """A bench PLC with no selector tag must behave as it always did."""

    station.plc.recipe_selector = "number"
    station.plc.recipe_number = 0
    station._handle_plc_state(station.plc.read_cycle_state())

    assert station.resolve_recipe_for_trigger("PLC") is station.active_recipe
