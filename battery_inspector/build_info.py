from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from battery_inspector import __version__

INSPECTION_ENGINE = "reference_registration_terminal_face_guard_ml_v2"
MANIFEST_SCHEMA_VERSION = 8
RECORD_SCHEMA_VERSION = 8


def _revision_from_archival_text(text: str) -> str:
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == "commit":
            candidate = value.strip()
            if candidate and "$Format" not in candidate:
                return candidate[:12]
    return "unknown"


def _archived_revision() -> str:
    """Read the commit substituted by ``git archive`` when .git is absent."""

    metadata_path = Path(__file__).with_name("_git_archival.txt")
    try:
        text = metadata_path.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    return _revision_from_archival_text(text)


@lru_cache(maxsize=1)
def _git_revision() -> str:
    """Return a best-effort source revision without making Git a runtime dependency."""

    injected = os.environ.get("POLARITY_TOOL_GIT_COMMIT", "").strip()
    if injected:
        return injected

    root = Path(__file__).resolve().parents[1]
    completed = None
    # Do not let Git walk into an unrelated parent repository when this source
    # archive was extracted inside another checkout.
    if (root / ".git").exists():
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=1.5,
            )
        except (OSError, subprocess.SubprocessError):
            completed = None
    if completed is not None:
        revision = completed.stdout.strip()
        if revision:
            return revision
    return _archived_revision()


def software_build_info() -> dict[str, Any]:
    return {
        "application": "Pole Position",
        "application_version": __version__,
        "git_commit": _git_revision(),
        "inspection_engine": INSPECTION_ENGINE,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "record_schema_version": RECORD_SCHEMA_VERSION,
    }
