from __future__ import annotations

import os
import sys
from pathlib import Path


STATION_DIRECTORY_NAME = "Pole Position"


def is_frozen() -> bool:
    """Return whether the application is running from a frozen executable."""

    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Return the read-only root containing bundled application resources.

    PyInstaller extracts/places collected data below ``sys._MEIPASS``. Source
    and editable installations keep those same resources at the repository
    root.  The environment override exists for packaging verification only.
    """

    override = str(os.environ.get("POLE_POSITION_RESOURCE_DIR", "") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        bundled = getattr(sys, "_MEIPASS", None)
        return Path(bundled or Path(sys.executable).resolve().parent).resolve()
    return Path(__file__).resolve().parents[1]


def station_root(*, create: bool = True) -> Path:
    """Return the writable station root used for configuration and runtime data.

    A Windows installer places executable resources under Program Files, which
    ordinary operators cannot modify. Frozen builds therefore keep all mutable
    state below the machine-wide ProgramData directory. Source checkouts retain
    the historical repository-local layout for compatibility with bench work.
    ``POLE_POSITION_HOME`` provides an explicit service/test deployment override.
    """

    override = str(os.environ.get("POLE_POSITION_HOME", "") or "").strip()
    if override:
        root = Path(override).expanduser()
    elif is_frozen():
        program_data = str(os.environ.get("PROGRAMDATA", "") or "").strip()
        base = Path(program_data) if program_data else Path.home() / "AppData" / "Local"
        root = base / STATION_DIRECTORY_NAME
    else:
        root = Path(__file__).resolve().parents[1]
    root = root.resolve()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root
