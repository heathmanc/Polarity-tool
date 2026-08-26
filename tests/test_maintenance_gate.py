"""Screen gating for ML Training and Settings.

This is a speed bump, not a security control, and the tests are written to
match that claim rather than to overstate it: they check that a wrong entry
does not open the screen, that the stored form is not the passcode itself, and
that every attempt reaches the audit log. They do not pretend the gate resists
anyone with the file system.
"""

from __future__ import annotations

import pytest

from battery_inspector.config import AppConfig
from battery_inspector.maintenance_passcode import (
    DEFAULT_PASSCODE,
    default_credentials,
    hash_passcode,
    new_salt,
    verify,
)
from battery_inspector.ui import MainWindow

GATED = (MainWindow.ML_TRAINING, MainWindow.SETTINGS)
OPEN_PAGES = (
    MainWindow.OVERVIEW,
    MainWindow.INSPECTION,
    MainWindow.RECIPES,
    MainWindow.DIAGNOSTICS,
    MainWindow.EVENTS,
)


@pytest.fixture()
def window(qapp, controller):
    # The shared controller fixture builds its config in code rather than
    # loading one, so it carries no passcode. A real station always has one --
    # AppConfig.load seeds the default when a file has none -- so give this
    # controller the same credentials a station would have.
    salt, digest = default_credentials()
    controller.config.maintenance_passcode_salt = salt
    controller.config.maintenance_passcode_hash = digest

    instance = MainWindow(controller)
    yield instance
    instance.close()
    qapp.processEvents()


def _answer(window: MainWindow, passcode: str | None) -> list[str]:
    """Replace the modal prompt with a fixed answer; record what it was asked."""

    asked: list[str] = []

    def prompt(screen: str) -> str | None:
        asked.append(screen)
        return passcode

    window.prompt_for_passcode = prompt  # type: ignore[method-assign]
    # The refusal notice is a modal too, and a modal in a headless test hangs.
    window.report_passcode_refused = lambda screen: None  # type: ignore[method-assign]
    return asked


# --- the passcode itself ---------------------------------------------------


def test_the_shipped_passcode_opens_a_default_station() -> None:
    salt, digest = default_credentials()

    assert verify(DEFAULT_PASSCODE, salt, digest) is True
    assert verify(DEFAULT_PASSCODE.lower(), salt, digest) is False
    assert verify("", salt, digest) is False


def test_the_passcode_is_not_stored_in_the_configuration(tmp_path) -> None:
    """A config file read over a shoulder must not simply show it."""

    path = tmp_path / "config.json"
    AppConfig.load(path)

    text = path.read_text(encoding="utf-8")
    assert DEFAULT_PASSCODE not in text


def test_each_station_gets_its_own_salt() -> None:
    """Two stations must not share a stored digest for the same passcode."""

    first_salt, first_digest = default_credentials()
    second_salt, second_digest = default_credentials()

    assert first_salt != second_salt
    assert first_digest != second_digest


def test_an_unconfigured_passcode_never_grants_access() -> None:
    """A blank digest must fail closed, not open the door."""

    assert verify("anything", new_salt(), "") is False
    assert verify("", "", "") is False


def test_a_station_without_a_passcode_is_given_the_default(tmp_path) -> None:
    """Upgrading must not leave the screens open or unreachable."""

    path = tmp_path / "config.json"
    path.write_text('{"operator_name": "Technician"}', encoding="utf-8")

    config = AppConfig.load(path)

    assert config.maintenance_passcode_hash
    assert verify(DEFAULT_PASSCODE, config.maintenance_passcode_salt, config.maintenance_passcode_hash)


def test_the_stored_digest_is_salted(tmp_path) -> None:
    salt = new_salt()
    other = new_salt()

    assert hash_passcode(DEFAULT_PASSCODE, salt) != hash_passcode(DEFAULT_PASSCODE, other)


# --- the gate --------------------------------------------------------------


@pytest.mark.parametrize("index", GATED)
def test_a_gated_screen_does_not_open_without_the_passcode(qapp, window, index) -> None:
    _answer(window, "wrong")
    before = window.stack.currentIndex()

    window.navigate(index)

    assert window.stack.currentIndex() == before


@pytest.mark.parametrize("index", GATED)
def test_cancelling_the_prompt_leaves_the_screen_closed(qapp, window, index) -> None:
    _answer(window, None)
    before = window.stack.currentIndex()

    window.navigate(index)

    assert window.stack.currentIndex() == before


@pytest.mark.parametrize("index", GATED)
def test_the_passcode_opens_the_screen(qapp, window, index) -> None:
    _answer(window, DEFAULT_PASSCODE)

    window.navigate(index)

    assert window.stack.currentIndex() == index


@pytest.mark.parametrize("index", OPEN_PAGES)
def test_production_screens_are_never_gated(qapp, window, index) -> None:
    """An operator must never be asked for a passcode to do their job."""

    asked = _answer(window, None)

    window.navigate(index)

    assert window.stack.currentIndex() == index
    assert asked == [], f"page {index} asked for a passcode"


def test_one_unlock_covers_both_screens(qapp, window) -> None:
    asked = _answer(window, DEFAULT_PASSCODE)

    window.navigate(MainWindow.SETTINGS)
    window.navigate(MainWindow.ML_TRAINING)

    assert window.stack.currentIndex() == MainWindow.ML_TRAINING
    assert len(asked) == 1, "the passcode was asked for twice in one session"


def test_logout_locks_the_screens_again(qapp, window) -> None:
    _answer(window, DEFAULT_PASSCODE)
    window.navigate(MainWindow.SETTINGS)
    assert window.stack.currentIndex() == MainWindow.SETTINGS

    window.navigate(MainWindow.OVERVIEW)
    window.lock_maintenance_screens()
    _answer(window, "wrong")
    window.navigate(MainWindow.SETTINGS)

    assert window.stack.currentIndex() == MainWindow.OVERVIEW


def test_the_sidebar_does_not_show_a_screen_that_did_not_open(qapp, window) -> None:
    """A refused page must not leave its nav button looking selected."""

    _answer(window, "wrong")

    window.navigate(MainWindow.SETTINGS)

    assert window.nav_buttons[MainWindow.SETTINGS].isChecked() is False
    assert window.nav_buttons[window.stack.currentIndex()].isChecked() is True


@pytest.mark.parametrize("granted,passcode", [(True, DEFAULT_PASSCODE), (False, "wrong")])
def test_every_attempt_reaches_the_audit_log(qapp, window, controller, granted, passcode) -> None:
    """The record is what makes the gate worth having.

    "Who opened Settings before that recipe changed" needs an answer, and so
    does "who was trying to".
    """

    _answer(window, passcode)

    window.navigate(MainWindow.SETTINGS)

    messages = [
        str(event.get("message", ""))
        for event in controller.repository.list_audit_events(limit=20)
    ]
    expected = "Settings opened" if granted else "Settings refused"
    assert any(expected in message for message in messages)


def test_a_configuration_with_no_passcode_locks_the_screens(qapp, controller) -> None:
    """Fail closed. An absent passcode must not mean an open door.

    AppConfig.load seeds the default for any station file without one, so this
    is the state of a config built in code rather than loaded -- but the gate
    must not assume that, because the consequence of guessing wrong is that
    every screen is open.
    """

    controller.config.maintenance_passcode_salt = ""
    controller.config.maintenance_passcode_hash = ""
    window = MainWindow(controller)
    try:
        _answer(window, DEFAULT_PASSCODE)
        window.navigate(MainWindow.SETTINGS)

        assert window.stack.currentIndex() != MainWindow.SETTINGS
    finally:
        window.close()
        qapp.processEvents()


def test_loading_a_configuration_never_rewrites_it(tmp_path) -> None:
    """A load that writes breaks a staged restore.

    A restore verifies config.json against the checksum in the backup manifest.
    Seeding the default passcode by writing it back changed the file during
    load, the checksum no longer matched, and the restore aborted -- the same
    class of failure that stranded a station earlier in this project.
    """

    path = tmp_path / "config.json"
    path.write_text('{"operator_name": "Technician"}', encoding="utf-8")
    before = path.read_bytes()

    config = AppConfig.load(path)

    assert path.read_bytes() == before, "loading the configuration modified it"
    # And the passcode still works in memory.
    assert verify(
        DEFAULT_PASSCODE,
        config.maintenance_passcode_salt,
        config.maintenance_passcode_hash,
    )
