from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from battery_inspector.config import AppConfig, MlConfig

BASELINE_ID = "v0.17-clean-circle-ml"
MARKER_NAME = ".clean_baseline_v017.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_clean_v017_baseline(
    project_root: Path,
    config: AppConfig,
    *,
    config_path: Path | None = None,
) -> tuple[AppConfig, dict[str, Any]]:
    """Archive pre-v0.17 bench runtime state once and start a clean baseline.

    v0.17 intentionally removes the legacy rectangular/four-class ML contract.
    The user's pre-v0.17 runtime was bench-only, so the safest migration is to
    archive it rather than silently mix incompatible recipes, datasets, and
    model packages with the new circle/three-class contract.

    Camera and PLC configuration live outside ``runtime`` and are preserved.
    Only the station ML path is reset because any installed pre-v0.17 model is
    contract-incompatible with the clean baseline.
    """

    normalized = config.normalized()
    data_directory = normalized.resolved_data_directory(project_root)
    marker = data_directory / MARKER_NAME
    if marker.is_file():
        return normalized, {
            "reset_performed": False,
            "baseline_id": BASELINE_ID,
            "marker": str(marker),
            "archive": "",
        }

    # Engineering escape hatch for recovery/testing. Normal station launches
    # should not set this variable.
    if os.environ.get("POLARITY_TOOL_SKIP_V017_BASELINE_RESET", "").strip() == "1":
        marker.write_text(
            json.dumps(
                {
                    "baseline_id": BASELINE_ID,
                    "created_at_utc": _utc_now(),
                    "reset_performed": False,
                    "skipped_by_environment": True,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return normalized, {
            "reset_performed": False,
            "baseline_id": BASELINE_ID,
            "marker": str(marker),
            "archive": "",
        }

    archive = data_directory / f"archive_pre_v017_{_safe_timestamp()}"
    moved: list[str] = []
    failed: list[str] = []
    ignored = {MARKER_NAME}
    entries = [item for item in data_directory.iterdir() if item.name not in ignored]
    entries = [item for item in entries if not item.name.startswith("archive_pre_v017_")]
    if entries:
        archive.mkdir(parents=True, exist_ok=False)
        for source in entries:
            destination = archive / source.name
            try:
                shutil.move(str(source), str(destination))
                moved.append(source.name)
            except OSError:
                failed.append(source.name)

    if failed:
        raise RuntimeError(
            "Could not complete the v0.17 bench-data archive. Close any process using: "
            + ", ".join(failed)
        )

    marker_payload = {
        "baseline_id": BASELINE_ID,
        "created_at_utc": _utc_now(),
        "reset_performed": bool(moved),
        "archive": str(archive) if archive.exists() else "",
        "moved_entries": moved,
        "failed_entries": failed,
        "note": (
            "Pre-v0.17 bench recipes, training samples, models, and evidence were "
            "archived so the active runtime starts on the circle/three-class ML contract."
        ),
    }
    marker.write_text(json.dumps(marker_payload, indent=2), encoding="utf-8")

    # Preserve camera/PLC/HMI settings but clear the station ML binding. Any old
    # model belongs to the archived legacy runtime contract.
    updated = replace(normalized, ml=MlConfig()).normalized()
    if config_path is not None:
        updated.save(config_path)

    return updated, {
        "reset_performed": bool(moved),
        "baseline_id": BASELINE_ID,
        "marker": str(marker),
        "archive": str(archive) if archive.exists() else "",
        "moved_entries": moved,
    }
