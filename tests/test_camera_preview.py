"""The live camera preview, and the guarantees that make it safe to run.

Tuning exposure or white balance by applying a profile and taking one test
frame is guesswork with a slow feedback loop, so the preview streams while a
control is moved. That means a production camera is being driven with settings
that are not saved and have not been validated against any recipe, and these
tests pin the three things that keep that from reaching a graded part.
"""

from __future__ import annotations

import dataclasses

import pytest

from battery_inspector.config import AppConfig, CameraConfig
from battery_inspector.controller import AppController
from battery_inspector.data import RecipeRepository

from conftest import ROOT, drain

GOOD_REFERENCE = ROOT / "battery_inspector" / "assets" / "demo_reference_good.png"


@pytest.fixture()
def station(qapp, tmp_path):
    root = tmp_path / "station"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    RecipeRepository(runtime / "battery_inspector.db").seed_demo_data(GOOD_REFERENCE)
    config = dataclasses.replace(
        AppConfig(),
        camera_backend="simulation",
        plc_backend="simulation",
        data_directory=str(runtime),
    )
    controller = AppController(root, config, resource_root=ROOT)
    controller.initialize()
    drain(qapp)
    yield controller
    controller.shutdown()
    qapp.processEvents()


def test_the_preview_streams_frames(qapp, station) -> None:
    frames: list[object] = []
    station.camera_preview_frame.connect(frames.append)

    assert station.start_camera_preview() is True
    assert station.camera_preview_active is True
    station._camera_preview_tick()
    drain(qapp)

    assert frames, "the preview produced no frame"
    assert frames[0] is not None

    station.stop_camera_preview()


def test_nothing_can_be_graded_while_the_preview_runs(qapp, station) -> None:
    """The guarantee that matters most.

    A preview may be driving the camera with an exposure a technician is still
    dragging. A part graded on it would carry a result nobody validated.
    """

    assert station.start_camera_preview() is True

    assert station.run_inspection("MANUAL") is False
    assert station.run_inspection("PLC") is False

    station.stop_camera_preview()
    drain(qapp)
    assert station.run_inspection("MANUAL") is True
    drain(qapp)


def test_the_preview_refuses_to_start_while_an_inspection_runs(qapp, station) -> None:
    assert station.run_inspection("MANUAL") is True

    assert station.start_camera_preview() is False
    assert station.camera_preview_active is False

    drain(qapp)


def test_preview_settings_are_never_written_to_the_station(qapp, station) -> None:
    saved = station.config.camera.normalized()
    station.start_camera_preview()

    tuned = dataclasses.replace(saved, exposure_auto="Off", exposure_us=9_999.0)
    station.preview_camera_settings(tuned)
    drain(qapp)

    assert station.config.camera.normalized() == saved, (
        "a preview must not change the station's saved camera profile"
    )
    station.stop_camera_preview()


def test_stopping_the_preview_restores_the_saved_settings(qapp, station) -> None:
    saved = station.config.camera.normalized()
    station.start_camera_preview()
    station.preview_camera_settings(
        dataclasses.replace(saved, exposure_auto="Off", exposure_us=1_234.0)
    )
    drain(qapp)

    station.stop_camera_preview(restore=True)
    drain(qapp)

    assert station.camera.settings.normalized().exposure_us == saved.exposure_us


def test_shutdown_stops_a_running_preview(qapp, tmp_path) -> None:
    """A technician who walks away must not leave the camera driven."""

    root = tmp_path / "station"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    config = dataclasses.replace(
        AppConfig(),
        camera_backend="simulation",
        plc_backend="simulation",
        data_directory=str(runtime),
    )
    controller = AppController(root, config, resource_root=ROOT)
    controller.initialize()
    drain(qapp)
    controller.start_camera_preview()
    assert controller.camera_preview_active is True

    controller.shutdown()
    qapp.processEvents()

    assert controller.camera_preview_active is False


def test_the_colour_settings_survive_a_configuration_round_trip(tmp_path) -> None:
    """What the new controls set has to still be there after a restart."""

    path = tmp_path / "config.json"
    config = dataclasses.replace(
        AppConfig(),
        camera=CameraConfig(
            balance_white_auto="Off",
            balance_ratio_red=1.25,
            balance_ratio_green=1.0,
            balance_ratio_blue=2.5,
            black_level_enabled=True,
            black_level=4.5,
            gamma_enabled=True,
            gamma=0.8,
        ).normalized(),
    )
    config.save(path)

    reloaded = AppConfig.load(path).camera

    assert reloaded.balance_white_auto == "Off"
    assert reloaded.balance_ratio_red == pytest.approx(1.25)
    assert reloaded.balance_ratio_blue == pytest.approx(2.5)
    assert reloaded.black_level_enabled is True
    assert reloaded.black_level == pytest.approx(4.5)
    assert reloaded.gamma_enabled is True
    assert reloaded.gamma == pytest.approx(0.8)


def test_colour_settings_default_to_leaving_the_camera_alone() -> None:
    """A station configured before these existed must be unaffected."""

    camera = CameraConfig().normalized()

    assert camera.balance_white_auto == "CameraDefault"
    assert camera.balance_ratio_red == 0.0
    assert camera.balance_ratio_green == 0.0
    assert camera.balance_ratio_blue == 0.0
    assert camera.black_level_enabled is False
    assert camera.gamma_enabled is False
