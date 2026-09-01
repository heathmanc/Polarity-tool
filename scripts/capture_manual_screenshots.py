"""Render the HMI screens the operator manual illustrates.

The manual has to show the application as it actually is at the version it
documents. Screenshots taken by hand drift: a control moves, a label is
reworded, and the manual quietly describes a screen that no longer exists.
Rendering them from the running widget tree means regenerating the manual's
figures is one command against a known commit.

The station is a temporary directory with simulation backends, so this touches
no real station data and needs no camera or PLC.

    python scripts/capture_manual_screenshots.py --output docs/manual/images

Requires an X server or, more usually, none at all:

    QT_QPA_PLATFORM=offscreen python scripts/capture_manual_screenshots.py
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# A 16:10 workspace comfortably above the station minimum, so the figures show
# the layout an operator sees rather than a compressed one.
CAPTURE_SIZE = (1600, 1000)


def _station(directory: Path, runtime: Path):
    """Simulation backends only: this needs no camera, no PLC, no real station."""

    import dataclasses

    from battery_inspector.config import AppConfig

    return dataclasses.replace(
        AppConfig(),
        camera_backend="simulation",
        plc_backend="simulation",
        data_directory=str(runtime),
    )


def settle(application, timeout: float = 60.0) -> None:
    """Let controller work running on the thread pool finish and report back."""

    import time

    from PySide6.QtCore import QThreadPool

    pool = QThreadPool.globalInstance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if pool.activeThreadCount() == 0:
            break
        time.sleep(0.01)
    pool.waitForDone(int(max(0.0, deadline - time.monotonic()) * 1000))
    application.processEvents()


def capture(output: Path) -> int:
    from PySide6.QtWidgets import QApplication

    from battery_inspector.controller import AppController
    from battery_inspector.ui import MainWindow

    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])

    with tempfile.TemporaryDirectory(prefix="pole_position_manual_") as temporary:
        station = Path(temporary)
        good_reference = ROOT / "battery_inspector" / "assets" / "demo_reference_good.png"

        # Seed before the controller opens the database, the way a commissioned
        # station already has recipes when it starts. An empty station
        # photographs as a page of dashes and teaches an operator nothing.
        from battery_inspector.data.repository import RecipeRepository

        runtime = station / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        RecipeRepository(runtime / "battery_inspector.db").seed_demo_data(good_reference)

        config = _station(station, runtime)
        controller = AppController(station, config, resource_root=ROOT)
        window = MainWindow(controller)
        try:
            window.resize(*CAPTURE_SIZE)
            # ML Training and Settings sit behind the maintenance passcode, and
            # a prompt nobody can answer hangs a headless capture forever. The
            # manual documents both screens, so unlock them up front.
            window.unlock_maintenance_screens()
            window.show()
            controller.initialize()
            settle(application)

            # Grade the accepted reference against itself, so the figures show a
            # real graded cycle: an image, the terminal overlays the manual
            # explains, timings, and counters.
            controller.camera.image_path = good_reference
            controller.run_inspection("MANUAL")
            settle(application)
            last = controller.last_inspection
            if last is None or not last.analysis_ready:
                print("WARNING: no graded cycle; figures will show a fault state.")

            written: list[Path] = []
            pages = [
                (MainWindow.OVERVIEW, "overview"),
                (MainWindow.INSPECTION, "inspection-detail"),
                (MainWindow.RECIPES, "recipes"),
                (MainWindow.ML_TRAINING, "ml-training"),
                (MainWindow.DIAGNOSTICS, "diagnostics"),
                (MainWindow.EVENTS, "events"),
                (MainWindow.SETTINGS, "settings"),
            ]
            for index, name in pages:
                window.navigate(index)
                application.processEvents()
                path = output / f"{name}.png"
                window.grab().save(str(path))
                written.append(path)

            # The ML training page is a sequence of steps behind one nav entry,
            # and the manual walks all of them.
            window.navigate(MainWindow.ML_TRAINING)
            application.processEvents()
            training = window.current_page()
            for step, name in enumerate(
                ("capture", "review", "prepare", "train", "deploy")
            ):
                training.stack.setCurrentIndex(step)
                application.processEvents()
                path = output / f"ml-{step + 1}-{name}.png"
                window.grab().save(str(path))
                written.append(path)

            # A reject is the case the manual has to teach, and it is the one
            # an empty or passing station cannot show. demo_battery.jpg is the
            # deliberately reversed demonstration part.
            reversed_part = ROOT / "battery_inspector" / "assets" / "demo_battery.jpg"
            if reversed_part.is_file():
                controller.camera.image_path = reversed_part
                controller.run_inspection("MANUAL")
                settle(application)
                last = controller.last_inspection
                if last is not None and last.disposition.value != "pass":
                    for index, name in (
                        (MainWindow.OVERVIEW, "overview-reject"),
                        (MainWindow.INSPECTION, "inspection-detail-reject"),
                        # Captured here rather than with the other pages: the
                        # review queue is only worth photographing once the
                        # station has actually rejected something, and an empty
                        # queue teaches an operator nothing.
                        (MainWindow.FAILURES, "failure-review"),
                    ):
                        window.navigate(index)
                        settle(application)
                        path = output / f"{name}.png"
                        window.grab().save(str(path))
                        written.append(path)
                else:
                    print("WARNING: the reversed part did not reject; no reject figures.")

            for path in written:
                print(f"{path.relative_to(ROOT)}  {path.stat().st_size:,} bytes")
            print(f"\n{len(written)} figures written to {output}")
            return 0
        finally:
            window.close()
            application.processEvents()
            controller.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render the HMI screens used as figures in the operator manual."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "manual" / "images",
        help="Directory to write the PNG figures into.",
    )
    return capture(parser.parse_args().output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
