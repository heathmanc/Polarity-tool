from __future__ import annotations

from pathlib import Path

from battery_inspector import paths


def test_source_layout_keeps_existing_repository_local_station_root(monkeypatch) -> None:
    monkeypatch.delenv("POLE_POSITION_HOME", raising=False)
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert paths.station_root(create=False) == Path(paths.__file__).resolve().parents[1]


def test_explicit_station_home_has_priority(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "station"
    monkeypatch.setenv("POLE_POSITION_HOME", str(target))
    assert paths.station_root() == target.resolve()
    assert target.is_dir()


def test_frozen_windows_layout_uses_programdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("POLE_POSITION_HOME", raising=False)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    assert paths.station_root(create=False) == (tmp_path / "Pole Position").resolve()


def test_resource_override_is_independent_from_station_home(monkeypatch, tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    station = tmp_path / "station"
    monkeypatch.setenv("POLE_POSITION_RESOURCE_DIR", str(resources))
    monkeypatch.setenv("POLE_POSITION_HOME", str(station))
    assert paths.resource_root() == resources.resolve()
    assert paths.station_root() == station.resolve()
