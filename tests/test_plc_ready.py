"""The station readiness tag.

Without it, the controller learns the station could not inspect only by
triggering and timing out -- and the states where a trigger is silently
dropped are exactly the ones a controller most wants to know about in advance:
a live camera preview, a reference capture, a validation run, an ML capture.
"""

from __future__ import annotations

import dataclasses

import pytest

from battery_inspector.config import AppConfig, PlcTagMap
from battery_inspector.controller import AppController
from battery_inspector.data import RecipeRepository

from conftest import ROOT, drain

GOOD_REFERENCE = ROOT / "battery_inspector" / "assets" / "demo_reference_good.png"


def _controller(tmp_path, qapp, *, ready_tag: str = "BatteryVision.Ready", seed: bool = True):
    root = tmp_path / "station"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    if seed:
        RecipeRepository(runtime / "battery_inspector.db").seed_demo_data(GOOD_REFERENCE)
    config = dataclasses.replace(
        AppConfig(),
        camera_backend="simulation",
        plc_backend="simulation",
        data_directory=str(runtime),
        tags=PlcTagMap(ready=ready_tag),
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


def test_a_commissioned_station_publishes_ready(qapp, station) -> None:
    assert station.station_ready_for_trigger() is True
    assert station.plc.snapshot()["ready"] is True


def test_a_station_with_no_recipe_is_not_ready(qapp, tmp_path) -> None:
    instance = _controller(tmp_path, qapp, seed=False)
    try:
        assert instance.station_ready_for_trigger() is False
        assert instance.plc.snapshot()["ready"] is False
    finally:
        instance.shutdown()
        qapp.processEvents()


def test_readiness_drops_while_a_live_preview_holds_the_camera(qapp, station) -> None:
    """The case that motivated the tag: a trigger here is dropped silently."""

    assert station.plc.snapshot()["ready"] is True

    station.start_camera_preview()

    assert station.station_ready_for_trigger() is False
    assert station.plc.snapshot()["ready"] is False

    station.stop_camera_preview()
    drain(qapp)

    assert station.plc.snapshot()["ready"] is True


def test_readiness_stays_true_while_an_inspection_runs(qapp, station) -> None:
    """Busy already reports the cycle. Ready reports capability.

    A readiness bit that dropped every cycle would flap at cycle rate and tell
    the controller nothing Busy did not already say. The permissive is
    Ready AND NOT Busy.
    """

    assert station.run_inspection("MANUAL") is True
    assert station.station_ready_for_trigger() is True
    drain(qapp)


def test_a_blank_tag_publishes_nothing(qapp, tmp_path) -> None:
    """A station commissioned before the tag existed must be unchanged."""

    instance = _controller(tmp_path, qapp, ready_tag="")
    try:
        assert instance.config.tags.ready == ""
        # The simulated PLC still reports its default; nothing was written.
        assert instance.plc.snapshot()["ready"] is False
        assert instance._plc_ready_published is None
    finally:
        instance.shutdown()
        qapp.processEvents()


def test_readiness_is_only_written_when_it_changes(qapp, station) -> None:
    """Health is recalculated constantly; the wire should not be."""

    writes: list[bool] = []
    original = station.plc.write_ready

    def counting(value: bool) -> bool:
        writes.append(bool(value))
        return original(value)

    station.plc.write_ready = counting  # type: ignore[method-assign]

    station._recalculate_system_health()
    station._recalculate_system_health()
    station._recalculate_system_health()

    assert writes == [], "readiness was rewritten without changing"


def test_the_ready_tag_defaults_to_blank() -> None:
    assert PlcTagMap().ready == ""
