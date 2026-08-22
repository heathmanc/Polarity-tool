from __future__ import annotations

from battery_inspector import __version__
from battery_inspector.build_info import (
    INSPECTION_ENGINE,
    MANIFEST_SCHEMA_VERSION,
    RECORD_SCHEMA_VERSION,
    _revision_from_archival_text,
    software_build_info,
)


def test_software_build_info_is_complete() -> None:
    payload = software_build_info()

    assert payload["application"] == "Pole Position"
    assert payload["application_version"] == __version__
    assert payload["git_commit"]
    assert payload["inspection_engine"] == INSPECTION_ENGINE
    assert payload["manifest_schema_version"] == MANIFEST_SCHEMA_VERSION == 8
    assert payload["record_schema_version"] == RECORD_SCHEMA_VERSION == 8


def test_taught_circle_engine_is_identified_explicitly() -> None:
    assert INSPECTION_ENGINE == "reference_registration_terminal_face_guard_ml_v2"


def test_git_archive_revision_substitution_is_supported() -> None:
    assert (
        _revision_from_archival_text(
            "commit: 0123456789abcdef0123456789abcdef01234567\n"
        )
        == "0123456789ab"
    )
    assert _revision_from_archival_text("commit: $Format:%H$\n") == "unknown"
