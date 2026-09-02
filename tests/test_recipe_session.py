"""What the PLC sees while a technician has a recipe open.

Busy used to mean only "an inspection cycle is running". That left the whole of
recipe editing and validation invisible on the wire: readiness dropped for the
fraction of a second a validation sample was being taken and came straight back,
so a controller watching Ready AND NOT Busy saw a station that looked available
between samples. It is not available -- somebody is standing at the fixture
placing parts by hand.

Busy is now held high for the entire session, so the controller has one
unambiguous interlock for a state that lasts minutes rather than milliseconds.
"""

from __future__ import annotations

import dataclasses

import pytest

from battery_inspector.config import AppConfig, PlcTagMap
from battery_inspector.controller import AppController
from battery_inspector.data import RecipeRepository

from conftest import ROOT, drain

GOOD_REFERENCE = ROOT / "battery_inspector" / "assets" / "demo_reference_good.png"


def _controller(tmp_path, qapp):
    root = tmp_path / "station"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    RecipeRepository(runtime / "battery_inspector.db").seed_demo_data(GOOD_REFERENCE)
    config = dataclasses.replace(
        AppConfig(),
        camera_backend="simulation",
        plc_backend="simulation",
        data_directory=str(runtime),
        tags=PlcTagMap(ready="BatteryVision.Ready"),
    )
    instance = AppController(root, config, resource_root=ROOT)
    instance.initialize()
    drain(qapp)
    return instance


@pytest.fixture()
def station(qapp, tmp_path):
    instance = _controller(tmp_path, qapp)
    yield instance
    instance.shutdown()
    qapp.processEvents()


def test_opening_a_recipe_holds_busy_high(qapp, station) -> None:
    station.begin_recipe_session()
    drain(qapp)

    snapshot = station.plc.snapshot()
    assert snapshot["busy"] is True
    assert snapshot["complete"] is False
    assert snapshot["passed"] is False
    assert snapshot["fail"] is False


def test_busy_stays_high_between_captures(qapp, station) -> None:
    """The whole point: it is a session, not a cycle.

    Nothing about finishing one validation sample makes the station available,
    because the technician is still standing there with the next part.
    """

    station.begin_recipe_session()
    drain(qapp)

    # Whatever else happens while the wizard is open, Busy does not drop.
    station._publish_plc_ready()
    station._recalculate_system_health()
    drain(qapp)

    assert station.plc.snapshot()["busy"] is True
    assert station.station_ready_for_trigger() is False


def test_readiness_goes_false_for_the_whole_session(qapp, station) -> None:
    assert station.station_ready_for_trigger() is True

    station.begin_recipe_session()
    drain(qapp)
    assert station.station_ready_for_trigger() is False
    assert station.plc.snapshot()["ready"] is False

    station.end_recipe_session()
    drain(qapp)
    assert station.station_ready_for_trigger() is True
    assert station.plc.snapshot()["ready"] is True


def test_closing_a_recipe_releases_busy(qapp, station) -> None:
    station.begin_recipe_session()
    drain(qapp)

    station.end_recipe_session()
    drain(qapp)

    snapshot = station.plc.snapshot()
    assert snapshot["busy"] is False
    assert snapshot["complete"] is False


def test_a_trigger_during_a_session_is_refused_and_logged_once(qapp, station) -> None:
    """The controller ignoring its own interlock must not grade a held part."""

    station.begin_recipe_session()
    drain(qapp)

    assert station.run_inspection("PLC") is False
    assert station.run_inspection("PLC") is False

    refusals = [
        event
        for event in station.audit_events()
        if event["category"] == "PLC" and "while a recipe was open" in event["message"]
    ]
    assert len(refusals) == 1


def test_a_manual_trigger_still_works_during_a_session(qapp, station) -> None:
    """The technician at the HMI is the one holding the station.

    Validation and reference captures run through their own paths, but a manual
    inspection is the technician's own deliberate action and must not be
    refused by their own session.
    """

    station.begin_recipe_session()
    drain(qapp)

    assert station.run_inspection("MANUAL") is True
    drain(qapp)


def test_the_session_is_re_asserted_after_the_plc_comes_back(qapp, station) -> None:
    """A controller that dropped and reconnected has no memory of Busy."""

    station.begin_recipe_session()
    drain(qapp)
    station.plc.clear_result()
    assert station.plc.snapshot()["busy"] is False

    station._assert_recipe_session_busy()
    drain(qapp)

    assert station.plc.snapshot()["busy"] is True


def test_beginning_twice_is_harmless(qapp, station) -> None:
    station.begin_recipe_session()
    station.begin_recipe_session()
    drain(qapp)
    station.end_recipe_session()
    drain(qapp)

    assert station.plc.snapshot()["busy"] is False
    assert station.station_ready_for_trigger() is True


def test_ending_without_beginning_is_harmless(qapp, station) -> None:
    station.end_recipe_session()
    drain(qapp)

    assert station.station_ready_for_trigger() is True


# --- the wizard drives the session ------------------------------------------


def test_the_wizard_opens_and_closes_the_session(qapp, station, monkeypatch) -> None:
    from battery_inspector.ui.pages import recipes as recipes_module

    seen: list[bool] = []

    class _Stub:
        recipe_ready = None

        def __init__(self, **_kwargs) -> None:
            self.recipe_ready = _Signal()

        def exec(self) -> int:
            seen.append(station.recipe_session_active)
            return 0

    class _Signal:
        def connect(self, _slot) -> None:
            return None

    monkeypatch.setattr(recipes_module, "RecipeWizardDialog", _Stub)
    page = recipes_module.RecipesPage(station)

    page._open_wizard()

    assert seen == [True]
    assert station.recipe_session_active is False
    assert station.plc.snapshot()["busy"] is False


def test_a_wizard_that_raises_still_releases_the_station(qapp, station, monkeypatch) -> None:
    """Busy held forever by a crash would stop the line and look like a fault."""

    from battery_inspector.ui.pages import recipes as recipes_module

    class _Signal:
        def connect(self, _slot) -> None:
            return None

    class _Exploding:
        def __init__(self, **_kwargs) -> None:
            self.recipe_ready = _Signal()

        def exec(self) -> int:
            raise RuntimeError("the wizard fell over")

    monkeypatch.setattr(recipes_module, "RecipeWizardDialog", _Exploding)
    page = recipes_module.RecipesPage(station)

    with pytest.raises(RuntimeError):
        page._open_wizard()

    assert station.recipe_session_active is False
    assert station.plc.snapshot()["busy"] is False
