"""Find and clear a stuck workstation-restore flag.

When a restore fails, older builds left the pending-import flag in place. The
station then re-attempts the same failing restore on every launch, and refuses
to import any other backup while the flag exists -- so the station is trapped.

Reinstalling the application does not help. Station data lives apart from the
program files, under ProgramData for an installed station, and the installer
preserves it deliberately. The flag is station data.

Run without arguments to see what is there:

    python scripts/clear_pending_restore.py

Then clear it:

    python scripts/clear_pending_restore.py --clear

Clearing removes only the pending flag and the staged copy it points at. The
station's own data -- recipes, models, evidence, configuration -- is untouched,
and a failed restore never modified it in the first place.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from battery_inspector.paths import STATION_DIRECTORY_NAME, station_root
from battery_inspector.station_transfer import (
    PENDING_RESTORE_NAME,
    RESTORE_RESULT_NAME,
    RESTORE_STAGING_DIRECTORY,
    ROLLBACK_DIRECTORY,
)


def candidate_station_roots(explicit: Path | None) -> list[Path]:
    """Every place a station root plausibly lives, most specific first."""

    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit).expanduser())

    override = str(os.environ.get("POLE_POSITION_HOME", "") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())

    # An installed station keeps its data here regardless of where the program
    # files went, which is why reinstalling never clears the flag.
    program_data = str(os.environ.get("PROGRAMDATA", "") or "").strip()
    if program_data:
        candidates.append(Path(program_data) / STATION_DIRECTORY_NAME)

    # A source checkout keeps station data beside the code.
    candidates.append(station_root(create=False))

    unique: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved not in unique:
            unique.append(resolved)
    return unique


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:,.1f} GB"


def describe(root: Path) -> dict[str, object]:
    marker = root / PENDING_RESTORE_NAME
    staging_root = root / RESTORE_STAGING_DIRECTORY
    rollbacks = sorted((root / ROLLBACK_DIRECTORY).glob("*.zip")) if (
        root / ROLLBACK_DIRECTORY
    ).is_dir() else []
    result_path = root / RESTORE_RESULT_NAME
    last_result: dict[str, object] = {}
    if result_path.is_file():
        try:
            last_result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            last_result = {"status": "unreadable"}
    return {
        "root": root,
        "exists": root.is_dir(),
        "marker": marker if marker.is_file() else None,
        "staging_root": staging_root if staging_root.is_dir() else None,
        "rollbacks": rollbacks,
        "last_result": last_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find and clear a stuck Pole Position restore flag."
    )
    parser.add_argument(
        "--station",
        type=Path,
        default=None,
        help="Station root to inspect. Detected automatically when omitted.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove the pending flag and its staged copy. Without this, only report.",
    )
    arguments = parser.parse_args()

    roots = candidate_station_roots(arguments.station)
    stuck = []

    for root in roots:
        state = describe(root)
        if not state["exists"]:
            print(f"[  ---  ] {root}  (no station data here)")
            continue

        marker = state["marker"]
        print(f"[STATION] {root}")
        if marker is None:
            print("          No pending restore flag. This station is not stuck.")
        else:
            stuck.append(state)
            print(f"          PENDING IMPORT FLAG: {marker.name}")
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                if payload.get("source_backup"):
                    print(f"          Staged from: {payload['source_backup']}")
                if payload.get("staged_at_utc"):
                    print(f"          Staged at  : {payload['staged_at_utc']}")
            except (OSError, json.JSONDecodeError):
                print("          (the flag file is unreadable, which is itself a reason to clear it)")

        last_result = state["last_result"]
        if last_result:
            status = last_result.get("status", "")
            print(f"          Last restore result: {status}")
            if last_result.get("error"):
                print(f"          Reported error     : {last_result['error']}")

        rollbacks = state["rollbacks"]
        if rollbacks:
            total = sum(item.stat().st_size for item in rollbacks)
            print(f"          Rollback archives  : {len(rollbacks)} using {human(total)}")
        print()

    if not stuck:
        print("Nothing to clear.")
        return 0

    if not arguments.clear:
        print("Re-run with --clear to remove the flag(s) above and allow importing again.")
        return 1

    for state in stuck:
        marker = state["marker"]
        try:
            marker.unlink()
            print(f"Cleared {marker}")
        except OSError as exc:
            print(f"Could not remove {marker}: {exc}")
            return 2
        staging_root = state["staging_root"]
        if staging_root is not None:
            shutil.rmtree(staging_root, ignore_errors=True)
            print(f"Removed staged copy {staging_root}")

    print()
    print("Start Pole Position normally, then import the backup again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
