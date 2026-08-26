"""Two station settings that decide what a trigger does.

Both replace something the station used to infer. Inference was wrong in the
same way each time: a value that meant "nothing was configured" was
indistinguishable from a value that meant "something is broken", and the
station guessed rather than refusing.
"""

from __future__ import annotations

import json

from battery_inspector.config import AppConfig, CameraConfig


def test_a_new_station_acquires_a_triggered_snapshot() -> None:
    assert AppConfig().normalized().camera.trigger_mode == "On"


def test_a_commissioned_station_keeps_free_run(tmp_path) -> None:
    """Changing the default must not change a station that is already running.

    Every release before this one wrote trigger_mode explicitly, so a real
    station's file carries "Off" and keeps it.
    """

    path = tmp_path / "config.json"
    AppConfig(camera=CameraConfig(trigger_mode="Off")).save(path)

    assert AppConfig.load(path).camera.trigger_mode == "Off"


def test_a_file_predating_the_field_keeps_free_run(tmp_path) -> None:
    """The one case a default flip could have changed silently."""

    path = tmp_path / "config.json"
    payload = json.loads(json.dumps({"camera": {"timeout_ms": 3000}}))
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert AppConfig.load(path).camera.trigger_mode == "Off"


def test_the_acquisition_mode_survives_a_save_and_load(tmp_path) -> None:
    path = tmp_path / "config.json"
    AppConfig(camera=CameraConfig(trigger_mode="On")).save(path)

    assert AppConfig.load(path).camera.trigger_mode == "On"


def test_the_recipe_source_defaults_to_the_plc() -> None:
    assert AppConfig().normalized().plc_recipe_source == "plc"


def test_an_unknown_recipe_source_falls_back_to_the_plc() -> None:
    """Never to the station selection: that is the substituting direction."""

    assert AppConfig(plc_recipe_source="whatever").normalized().plc_recipe_source == "plc"


def test_the_recipe_source_survives_a_save_and_load(tmp_path) -> None:
    path = tmp_path / "config.json"
    AppConfig(plc_recipe_source="station").save(path)

    assert AppConfig.load(path).plc_recipe_source == "station"
