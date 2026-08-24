"""The recovery tool for a station stuck on a failed import.

Older builds left the pending-import flag in place when a restore failed, which
traps the station: it re-attempts the same failing restore on every launch and
refuses any other import while the flag exists. Reinstalling the application
cannot help, because the flag is station data and the installer preserves
station data deliberately.
"""

from __future__ import annotations

import json
import runpy
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from battery_inspector.config import AppConfig
from battery_inspector.station_transfer import (
    PENDING_RESTORE_NAME,
    create_station_backup,
    stage_station_restore,
)

from conftest import ROOT

TOOL = ROOT / "scripts" / "clear_pending_restore.py"


def _run(*arguments: str) -> tuple[int, str]:
    argv = sys.argv
    sys.argv = ["clear_pending_restore.py", *arguments]
    buffer = StringIO()
    try:
        with redirect_stdout(buffer):
            try:
                runpy.run_path(str(TOOL), run_name="__main__")
                code = 0
            except SystemExit as exit_code:
                code = int(exit_code.code or 0)
    finally:
        sys.argv = argv
    return code, buffer.getvalue()


@pytest.fixture()
def stuck_station(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    data = source / "runtime"
    data.mkdir()
    (data / "note.txt").write_text("source data", encoding="utf-8")
    config_path = source / "config.json"
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(config_path)
    backup = tmp_path / "backup.zip"
    create_station_backup(source, config_path, data, backup)

    station = tmp_path / "station"
    station.mkdir()
    station_data = station / "runtime"
    station_data.mkdir()
    (station_data / "keep.txt").write_text("existing station data", encoding="utf-8")
    AppConfig(camera_backend="simulation", plc_backend="simulation").save(
        station / "config.json"
    )
    stage_station_restore(station, backup)
    (station / ".pole_position_restore_result.json").write_text(
        json.dumps({"status": "failed", "error": "[WinError 32] locked file"}),
        encoding="utf-8",
    )
    return station


def test_reporting_finds_the_flag_without_changing_anything(stuck_station: Path) -> None:
    code, output = _run("--station", str(stuck_station))

    assert code == 1, "a stuck station must not report success"
    assert "PENDING IMPORT FLAG" in output
    assert (stuck_station / PENDING_RESTORE_NAME).is_file()


def test_the_report_explains_why_the_restore_failed(stuck_station: Path) -> None:
    _code, output = _run("--station", str(stuck_station))

    assert "Last restore result: failed" in output
    assert "WinError 32" in output


def test_clearing_removes_the_flag_and_the_staged_copy(stuck_station: Path) -> None:
    code, output = _run("--station", str(stuck_station), "--clear")

    assert code == 0
    assert not (stuck_station / PENDING_RESTORE_NAME).exists()
    assert not (stuck_station / ".pole_position_restore_staging").exists()
    assert "import the backup again" in output


def test_clearing_never_touches_station_data(stuck_station: Path) -> None:
    """A failed restore left the station unchanged; recovery must too."""

    _run("--station", str(stuck_station), "--clear")

    assert (stuck_station / "runtime" / "keep.txt").read_text(
        encoding="utf-8"
    ) == "existing station data"
    assert (stuck_station / "config.json").is_file()


def test_a_healthy_station_reports_nothing_to_clear(tmp_path: Path) -> None:
    station = tmp_path / "healthy"
    station.mkdir()

    code, output = _run("--station", str(station))

    assert code == 0
    assert "not stuck" in output


def test_an_installed_station_is_found_through_programdata(
    stuck_station: Path, monkeypatch
) -> None:
    """Reinstalling cannot clear the flag, so the tool has to locate it."""

    monkeypatch.setenv("PROGRAMDATA", str(stuck_station.parent))
    monkeypatch.delenv("POLE_POSITION_HOME", raising=False)
    renamed = stuck_station.parent / "Pole Position"
    stuck_station.rename(renamed)

    code, output = _run()

    assert code == 1
    assert "PENDING IMPORT FLAG" in output
    assert str(renamed) in output


# --- the tool must keep working on a broken station -------------------------


def test_the_tool_needs_nothing_from_the_application(tmp_path: Path) -> None:
    """It runs where the package is not importable, which is the point.

    A stuck station may have no source checkout and no working environment. The
    first version imported battery_inspector and died with ModuleNotFoundError
    for exactly the operator who needed it.
    """

    import subprocess

    result = subprocess.run(
        [sys.executable, str(TOOL), "--station", str(tmp_path)],
        capture_output=True,
        text=True,
        # An empty path plus a working directory outside the repository means
        # battery_inspector cannot be imported by any means.
        cwd=str(tmp_path),
        env={
            **{k: v for k, v in __import__("os").environ.items() if k != "PYTHONPATH"},
            "PYTHONPATH": "",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "Nothing to clear" in result.stdout


def test_the_duplicated_constants_still_match_the_application() -> None:
    """The tool copies these names precisely so it can stand alone."""

    import ast

    from battery_inspector import paths, station_transfer

    # Read the constants from the source rather than importing the tool: an
    # import can serve a stale .pyc, and two spellings of the same length do not
    # always invalidate that cache.
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    declared = {
        target.id: statement.value.value
        for statement in tree.body
        if isinstance(statement, ast.Assign)
        and isinstance(statement.value, ast.Constant)
        for target in statement.targets
        if isinstance(target, ast.Name)
    }

    assert declared["STATION_DIRECTORY_NAME"] == paths.STATION_DIRECTORY_NAME
    assert declared["PENDING_RESTORE_NAME"] == station_transfer.PENDING_RESTORE_NAME
    assert declared["RESTORE_RESULT_NAME"] == station_transfer.RESTORE_RESULT_NAME
    assert declared["RESTORE_STAGING_DIRECTORY"] == station_transfer.RESTORE_STAGING_DIRECTORY
    assert declared["ROLLBACK_DIRECTORY"] == station_transfer.ROLLBACK_DIRECTORY
