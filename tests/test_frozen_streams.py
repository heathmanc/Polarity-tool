"""A windowed frozen build must still have somewhere to print.

PyInstaller builds this application with console=False, and such a process has
sys.stdout and sys.stderr set to None. Anything that prints then raises
"AttributeError: 'NoneType' object has no attribute 'write'". Ultralytics prints
training progress, so model training failed immediately in a packaged build
while working from source.
"""

from __future__ import annotations

import sys
from pathlib import Path

from battery_inspector.main import _ensure_standard_streams


def test_streams_are_replaced_when_the_build_has_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    _ensure_standard_streams(tmp_path)

    assert sys.stdout is not None
    assert sys.stderr is not None
    # The failing call in the field was exactly this one.
    sys.stdout.write("training progress\n")
    sys.stderr.write("a warning\n")
    sys.stdout.flush()


def test_the_replacement_captures_output_for_diagnosis(tmp_path: Path, monkeypatch) -> None:
    """A training run that fails is when its output is most wanted."""

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    _ensure_standard_streams(tmp_path)
    print("epoch 1/20")
    sys.stdout.flush()

    log = tmp_path / "logs" / "pole-position.log"
    assert log.is_file()
    assert "epoch 1/20" in log.read_text(encoding="utf-8")


def test_working_streams_are_left_alone(tmp_path: Path) -> None:
    """Running from source must not have its console redirected to a file."""

    before_out, before_err = sys.stdout, sys.stderr

    _ensure_standard_streams(tmp_path)

    assert sys.stdout is before_out
    assert sys.stderr is before_err
    assert not (tmp_path / "logs").exists()


def test_an_unwritable_station_still_yields_usable_streams(tmp_path: Path, monkeypatch) -> None:
    """Losing the log must not reintroduce the crash it was added to prevent."""

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    real_mkdir = Path.mkdir

    def refuse(self, *args, **kwargs):
        if self.name == "logs":
            raise PermissionError("read-only station")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", refuse)

    _ensure_standard_streams(tmp_path)

    assert sys.stdout is not None
    sys.stdout.write("still writable\n")
