from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Keep OpenCV deterministic and prevent nested native thread pools from
# accumulating across the full image-heavy regression suite. Production code
# retains OpenCV's normal thread policy.
try:
    import cv2

    cv2.setNumThreads(1)
except ImportError:
    pass


# Qt must run without a display server for the HMI tests. setdefault keeps an
# explicitly exported platform (a developer running them against a real screen).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --- Qt fixtures -----------------------------------------------------------
#
# The HMI is the largest subsystem in the project and was previously verified
# only by asserting on page source text, which cannot catch constructor or
# signal-payload drift. These fixtures let tests build the real controller and
# the real widget tree headlessly, on any machine and on CI, without a display
# server and without touching a camera, a PLC, or the station's data directory.

@pytest.fixture(scope="session")
def qapp():
    """The single QApplication the widget tests share.

    Qt permits exactly one QApplication per process, so this is session-scoped
    and never destroyed; pytest tears the process down instead.
    """

    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.fixture()
def station(tmp_path):
    """An isolated station root with simulation-only backends.

    Both backends are pinned to simulation rather than left on the "auto"
    default so a machine that happens to have pypylon installed cannot pull a
    test onto real hardware.
    """

    import dataclasses

    from battery_inspector.config import AppConfig

    root = tmp_path / "station"
    root.mkdir()
    config = dataclasses.replace(
        AppConfig(),
        camera_backend="simulation",
        plc_backend="simulation",
        data_directory=str(root / "runtime"),
    )
    return root, config


@pytest.fixture()
def controller(qapp, station):
    """A real AppController rooted in a temporary station directory."""

    from battery_inspector.controller import AppController

    root, config = station
    instance = AppController(root, config, resource_root=ROOT)
    yield instance
    instance.shutdown()
    qapp.processEvents()


def drain(qapp, *, timeout: float = 60.0) -> None:
    """Run queued Qt events until the controller's thread-pool work completes.

    Controller service tasks run on QThreadPool and report back through queued
    signals, so both the pool and the event queue have to settle before a test
    can assert on the result.
    """

    import time

    from PySide6.QtCore import QThreadPool

    pool = QThreadPool.globalInstance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if pool.activeThreadCount() == 0:
            break
        time.sleep(0.01)
    pool.waitForDone(int(max(0.0, deadline - time.monotonic()) * 1000))
    qapp.processEvents()
