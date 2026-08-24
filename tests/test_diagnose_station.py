"""The diagnostic that explains why a station grades the way it does.

Written after a station began passing parts that should have been rejected. The
report exists so the cause comes from the station's own stored state instead of
from guesswork.
"""

from __future__ import annotations

import dataclasses
import runpy
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from battery_inspector.config import AppConfig
from battery_inspector.data import RecipeRepository

from conftest import ROOT

TOOL = ROOT / "scripts" / "diagnose_station.py"
ASSETS = ROOT / "battery_inspector" / "assets"


def _station(tmp_path: Path, **overrides) -> Path:
    root = tmp_path / "station"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    RecipeRepository(runtime / "battery_inspector.db").seed_demo_data(
        ASSETS / "demo_reference_good.png"
    )
    settings = {
        "camera_backend": "basler",
        "plc_backend": "pycomm3",
        "data_directory": str(runtime),
        **overrides,
    }
    config = dataclasses.replace(AppConfig(), **settings)
    config.save(root / "config.json")
    return root


def _run(station: Path) -> tuple[int, str]:
    argv = sys.argv
    sys.argv = ["diagnose_station.py", "--station", str(station)]
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


def test_a_simulated_camera_is_reported_as_a_finding(qapp, tmp_path: Path) -> None:
    """It grades a bundled image, so its verdict says nothing about the part."""

    station = _station(tmp_path, camera_backend="simulation")

    code, output = _run(station)

    assert code == 1
    assert "SIMULATION" in output
    assert "not the battery on the fixture" in output


def test_the_report_names_the_active_recipe_and_its_gates(qapp, tmp_path: Path) -> None:
    station = _station(tmp_path)

    _code, output = _run(station)

    assert "GROUP31_XHD" in output
    assert "expects=minus" in output
    assert "expects=plus" in output
    assert "red_ring_required" in output


def test_a_recipe_whose_terminals_all_expect_one_marking_is_a_finding(
    qapp, tmp_path: Path
) -> None:
    """Then a reversed battery cannot be told from a correct one."""

    station = _station(tmp_path)
    repository = RecipeRepository(station / "runtime" / "battery_inspector.db")
    recipe = repository.get_active_recipe()
    assert recipe is not None
    for terminal in recipe.terminals:
        terminal.expected_marking = recipe.terminals[0].expected_marking
    repository.save_recipe(recipe, username="test", message="single expectation")
    repository.activate_recipe(recipe.recipe_id, recipe.revision, username="test")

    code, output = _run(station)

    assert code == 1
    assert "Every terminal expects the same marking" in output
    assert "reversed battery cannot be distinguished" in output


def test_a_missing_model_is_not_flagged_for_a_template_recipe(qapp, tmp_path: Path) -> None:
    """The seeded recipe grades by reference template and never consults it."""

    station = _station(tmp_path)

    _code, output = _run(station)

    assert "this recipe does not use ML" in output
    assert "The installed model file is missing" not in output


def test_the_report_explains_the_unavailable_export(qapp, tmp_path: Path) -> None:
    """A PASS writes no evidence, which is why the export button is disabled."""

    station = _station(tmp_path)

    _code, output = _run(station)

    assert "EXPORT INSPECTION ZIP" in output
    assert "memory-only" in output


def test_a_station_without_configuration_says_so(qapp, tmp_path: Path) -> None:
    code, output = _run(tmp_path / "nowhere")

    assert code == 2
    assert "No station configuration" in output


def test_the_diagnostic_grades_nothing(qapp, tmp_path: Path) -> None:
    """It must be safe to run on a live station: it reads, it does not inspect."""

    station = _station(tmp_path)
    source = TOOL.read_text(encoding="utf-8")

    _run(station)

    assert "run_inspection" not in source
    assert "initialize()" not in source
    summary = RecipeRepository(
        station / "runtime" / "battery_inspector.db"
    ).inspection_summary()
    assert summary["part_count"] == 0
