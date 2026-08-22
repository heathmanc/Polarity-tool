"""Tests for the measured station-health indicators.

Lighting and disk were previously hardcoded: the health bar always reported
lighting OK and disk "82% FREE", and the Diagnostics storage bar was a fixed
18%/82% mock. On a station whose job is writing failure evidence to disk, a
fabricated free-space reading is the one placeholder that can actively mislead
a technician, so these tests pin the measured behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from battery_inspector.controller import LIGHTING_HEALTH_UNMONITORED
from battery_inspector.paths import (
    LOW_DISK_FLOOR_BYTES,
    LOW_DISK_FLOOR_RATIO,
    disk_health,
)


class _Usage:
    def __init__(self, total: int, free: int) -> None:
        self.total = total
        self.free = free
        self.used = total - free


def _patch_usage(monkeypatch, total: int, free: int) -> None:
    monkeypatch.setattr(
        "battery_inspector.paths.shutil.disk_usage",
        lambda _path: _Usage(total, free),
    )


# --- the probe -------------------------------------------------------------


def test_disk_health_measures_the_real_volume(tmp_path) -> None:
    state = disk_health(tmp_path)

    assert state["measured"] is True
    assert state["text"].endswith("% FREE")
    assert 0 <= float(state["free_percent"]) <= 100
    assert int(state["total_bytes"]) > 0
    # The reading must describe the volume, not a constant.
    assert state["text"] != "82% FREE" or float(state["free_percent"]) != 82.0


def test_a_healthy_volume_reports_its_free_percentage(monkeypatch, tmp_path) -> None:
    _patch_usage(monkeypatch, total=500 * 1024**3, free=200 * 1024**3)

    state = disk_health(tmp_path)

    assert state["ok"] is True
    assert state["text"] == "40% FREE"
    assert state["used_percent"] == pytest.approx(60.0)


def test_a_large_volume_faults_on_the_absolute_byte_floor(monkeypatch, tmp_path) -> None:
    """A comfortable percentage can still be too few bytes to keep working."""

    _patch_usage(
        monkeypatch,
        total=10_000 * 1024**3,
        free=LOW_DISK_FLOOR_BYTES - 1,
    )

    state = disk_health(tmp_path)

    assert state["ok"] is False
    assert state["measured"] is True


def test_a_small_volume_faults_on_the_ratio_floor(monkeypatch, tmp_path) -> None:
    """And plenty of bytes can still be too small a share of a small volume."""

    total = 200 * 1024**3
    _patch_usage(monkeypatch, total=total, free=int(total * (LOW_DISK_FLOOR_RATIO / 2)))

    state = disk_health(tmp_path)

    assert state["ok"] is False
    assert int(state["free_bytes"]) > LOW_DISK_FLOOR_BYTES


def test_an_unreadable_volume_reports_unknown_rather_than_healthy(tmp_path) -> None:
    state = disk_health(tmp_path / "no" / "such" / "directory")

    assert state["ok"] is False
    assert state["measured"] is False
    assert state["text"] == "UNKNOWN"


def test_a_zero_sized_volume_is_not_reported_as_healthy(monkeypatch, tmp_path) -> None:
    _patch_usage(monkeypatch, total=0, free=0)

    state = disk_health(tmp_path)

    assert state["ok"] is False
    assert state["text"] == "UNKNOWN"


# --- the controller --------------------------------------------------------


def test_controller_publishes_a_measured_disk_reading(controller) -> None:
    disk = controller.health["disk"]

    assert disk["measured"] is True
    assert disk["text"].endswith("% FREE")


def test_health_recalculation_refreshes_the_disk_reading(controller, monkeypatch) -> None:
    _patch_usage(monkeypatch, total=500 * 1024**3, free=250 * 1024**3)

    controller._recalculate_system_health()

    assert controller.health["disk"]["text"] == "50% FREE"


def test_lighting_reports_that_it_is_not_measured(controller) -> None:
    lighting = controller.health["lighting"]

    assert lighting["text"] == "NOT MONITORED"
    # Nothing has detected a lighting fault, so this is not an alarm state.
    assert lighting["ok"] is True


def test_the_lighting_default_cannot_be_mutated_through_the_health_dict(controller) -> None:
    controller.health["lighting"]["text"] = "MUTATED"

    assert LIGHTING_HEALTH_UNMONITORED["text"] == "NOT MONITORED"


def test_low_disk_does_not_take_the_station_out_of_production(controller, monkeypatch) -> None:
    """Run state is a change-controlled contract; disk reports without gating."""

    controller.health["camera"] = {"ok": True, "text": "SIMULATION"}
    controller.health["plc"] = {"ok": True, "text": "SIMULATION"}
    _patch_usage(monkeypatch, total=500 * 1024**3, free=1024**2)

    controller._recalculate_system_health()

    assert controller.health["disk"]["ok"] is False
    assert controller.health["system"]["text"] != "DEGRADED"


def test_station_health_probe_targets_the_data_directory(controller) -> None:
    assert Path(controller.data_directory).is_dir()
    assert disk_health(controller.data_directory)["measured"] is True
