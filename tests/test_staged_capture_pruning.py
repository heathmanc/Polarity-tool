"""Staged reference captures must not accumulate for the life of a station.

Every capture in the recipe wizard or the ML training page writes a
full-resolution lossless frame into a staging directory -- tens of megabytes
each. Accepting one copies it into an immutable recipe revision or the sample
store, which is what the station then uses, so the staged original is redundant
from that moment; an abandoned one is never referenced again. Nothing removed
them, and on a real station 104 of them reached 1.2 GB, dominating both the
station's disk and its backups.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from battery_inspector.evidence import (
    STAGED_CAPTURE_MAX_AGE_DAYS,
    prune_staged_captures,
)


def _capture(directory: Path, name: str, *, age_days: float, size: int = 2048) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"x" * size)
    when = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
    os.utime(path, (when, when))
    return path


def test_captures_past_the_window_are_removed(tmp_path: Path) -> None:
    staging = tmp_path / "ml_training" / "staging"
    stale = _capture(staging, "reference-old.png", age_days=STAGED_CAPTURE_MAX_AGE_DAYS + 1)

    summary = prune_staged_captures([staging])

    assert not stale.exists()
    assert summary["removed_count"] == 1
    assert summary["reclaimed_bytes"] == 2048
    assert summary["failed_count"] == 0


def test_a_capture_still_inside_the_window_is_kept(tmp_path: Path) -> None:
    """An open wizard session must never lose the capture it is working on."""

    staging = tmp_path / "recipe_staging"
    fresh = _capture(staging, "reference-current.png", age_days=0)

    summary = prune_staged_captures([staging])

    assert fresh.exists()
    assert summary["removed_count"] == 0


def test_both_staging_directories_are_swept(tmp_path: Path) -> None:
    ml = tmp_path / "ml_training" / "staging"
    recipes = tmp_path / "recipe_staging"
    _capture(ml, "reference-a.png", age_days=30)
    _capture(recipes, "reference-b.png", age_days=30)

    summary = prune_staged_captures([ml, recipes])

    assert summary["removed_count"] == 2


def test_only_files_this_application_named_are_touched(tmp_path: Path) -> None:
    staging = tmp_path / "recipe_staging"
    _capture(staging, "reference-a.png", age_days=30)
    keepsake = _capture(staging, "technician-notes.png", age_days=30)
    subdirectory = staging / "nested"
    subdirectory.mkdir()

    summary = prune_staged_captures([staging])

    assert summary["removed_count"] == 1
    assert keepsake.exists()
    assert subdirectory.is_dir()


def test_a_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    summary = prune_staged_captures([tmp_path / "never-created"])

    assert summary == {"removed_count": 0, "reclaimed_bytes": 0, "failed_count": 0}


def test_an_undeletable_capture_is_counted_rather_than_raised(tmp_path: Path, monkeypatch) -> None:
    """Losing scratch space must never stop a station from starting."""

    staging = tmp_path / "recipe_staging"
    _capture(staging, "reference-locked.png", age_days=30)

    def refuse(self):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(Path, "unlink", refuse)

    summary = prune_staged_captures([staging])

    assert summary["failed_count"] == 1
    assert summary["removed_count"] == 0


def test_the_window_is_far_longer_than_any_wizard_session() -> None:
    assert STAGED_CAPTURE_MAX_AGE_DAYS >= 7
