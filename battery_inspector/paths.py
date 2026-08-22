from __future__ import annotations

import os
import shutil
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


# Free-space floors for the station health indicator. A station writes failure
# evidence, ML training samples, and workstation backups to this volume, so the
# indicator has to fault well before the volume is actually full: the absolute
# floor covers small volumes where a percentage stays comfortable while the
# remaining bytes cannot hold a retention budget, and the ratio covers large
# volumes where 2 GB is already the last gasp.
LOW_DISK_FLOOR_BYTES = 2 * 1024**3
LOW_DISK_FLOOR_RATIO = 0.05


def disk_health(path: Path) -> dict[str, object]:
    """Measure free space on the volume that holds the station data directory.

    Returned in the shape the HMI health bar consumes. An unreadable or missing
    path reports UNKNOWN rather than a comfortable default: the indicator exists
    to tell a technician when evidence can no longer be written, so it must not
    claim capacity it has not measured.
    """

    try:
        usage = shutil.disk_usage(path)
    except (OSError, ValueError):
        return {"ok": False, "text": "UNKNOWN", "measured": False}
    if usage.total <= 0:
        return {"ok": False, "text": "UNKNOWN", "measured": False}

    free_ratio = usage.free / usage.total
    healthy = usage.free >= LOW_DISK_FLOOR_BYTES and free_ratio >= LOW_DISK_FLOOR_RATIO
    return {
        "ok": healthy,
        "text": f"{free_ratio * 100:.0f}% FREE",
        "measured": True,
        "free_bytes": int(usage.free),
        "total_bytes": int(usage.total),
        "used_percent": round((1.0 - free_ratio) * 100.0, 1),
        "free_percent": round(free_ratio * 100.0, 1),
    }
