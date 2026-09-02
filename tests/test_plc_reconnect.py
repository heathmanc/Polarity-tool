"""Getting the PLC back without a technician walking to the HMI.

A lost connection used to be terminal: both timers stopped, the station went to
FAULT, and nothing tried again until somebody pressed APPLY & TEST. A switch
reboot, a controller download, or a cable knocked at shift change took the
station out for as long as it took a person to notice.

"Never falls back to Simulation" and "never retries" are different rules. These
tests hold the second one, and hold the first one while doing it: reconnection
re-establishes the configured backend and nothing else, and the station stays
faulted until a real read succeeds.
"""

from __future__ import annotations

import dataclasses

import pytest

from battery_inspector.config import AppConfig, PlcTagMap
from battery_inspector.controller import AppController
from battery_inspector.data import RecipeRepository
from battery_inspector.services.plc import MockPlcService, PlcError

from conftest import ROOT, drain

GOOD_REFERENCE = ROOT / "battery_inspector" / "assets" / "demo_reference_good.png"


class FlakyPlcService(MockPlcService):
    """A physical-style service whose link can be cut and restored.

    Subclasses the mock for its handshake behaviour but reports itself as a
    hardware backend, because a simulated PLC is deliberately never reconnected.
    """

    def __init__(self) -> None:
        super().__init__()
        self.link_up = True
        self.connect_calls = 0
        self.read_calls = 0

    def connect(self) -> None:
        self.connect_calls += 1
        if not self.link_up:
            raise PlcError("Simulated network is down")
        super().connect()

    def read_cycle_state(self):
        self.read_calls += 1
        if not self.link_up:
            raise PlcError("Simulated network is down")
        return super().read_cycle_state()


def _controller(tmp_path, qapp):
    root = tmp_path / "station"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    RecipeRepository(runtime / "battery_inspector.db").seed_demo_data(GOOD_REFERENCE)
    config = dataclasses.replace(
        AppConfig(),
        camera_backend="simulation",
        plc_backend="simulation",
        data_directory=str(runtime),
        tags=PlcTagMap(ready="BatteryVision.Ready"),
    )
    instance = AppController(root, config, resource_root=ROOT)
    instance.initialize()
    drain(qapp)
    return instance


@pytest.fixture()
def station(qapp, tmp_path):
    instance = _controller(tmp_path, qapp)
    yield instance
    instance.shutdown()
    qapp.processEvents()


@pytest.fixture()
def wired(qapp, station):
    """A station whose PLC is a hardware-style service we can cut."""

    service = FlakyPlcService()
    service.connect()
    station.plc = service
    station.plc_backend_active = "pycomm3"
    return station


def test_a_lost_connection_schedules_a_reconnect(qapp, wired) -> None:
    wired.plc.link_up = False

    wired._plc_poll_failed("read timed out")
    drain(qapp)

    assert wired.plc_reconnect_timer.isActive() is True
    assert wired.health["plc"]["ok"] is False
    assert wired.plc_poll_timer.isActive() is False


def test_the_failure_is_logged_once_not_per_attempt(qapp, wired) -> None:
    """A controller down overnight must not write thousands of identical rows."""

    wired.plc.link_up = False
    for _ in range(5):
        wired._plc_poll_failed("read timed out")
    drain(qapp)

    stopped = [
        event
        for event in wired.audit_events()
        if event["category"] == "PLC" and "polling stopped" in event["message"]
    ]
    assert len(stopped) == 1


def test_the_backoff_grows_and_is_capped(qapp, wired) -> None:
    wired.plc.link_up = False
    delays: list[int] = []
    for _ in range(10):
        wired.plc_reconnect_timer.stop()
        wired._schedule_plc_reconnect()
        delays.append(wired._plc_reconnect_delay_ms)

    assert delays[0] == AppController.PLC_RECONNECT_FIRST_MS
    assert delays == sorted(delays)
    assert max(delays) == AppController.PLC_RECONNECT_MAX_MS


def test_a_failed_attempt_reschedules_and_stays_faulted(qapp, wired) -> None:
    wired.plc.link_up = False
    wired._plc_poll_failed("read timed out")
    drain(qapp)

    wired._attempt_plc_reconnect()
    drain(qapp)

    assert wired.health["plc"]["ok"] is False
    assert wired.plc_poll_timer.isActive() is False
    assert wired.plc_reconnect_timer.isActive() is True


def test_the_station_recovers_when_the_link_comes_back(qapp, wired) -> None:
    wired.plc.link_up = False
    wired._plc_poll_failed("read timed out")
    drain(qapp)

    wired.plc.link_up = True
    wired._attempt_plc_reconnect()
    drain(qapp)

    assert wired.health["plc"]["ok"] is True
    assert wired.plc_poll_timer.isActive() is True
    assert wired.plc_heartbeat_timer.isActive() is True
    assert wired.plc_reconnect_timer.isActive() is False
    recovered = [
        event
        for event in wired.audit_events()
        if event["category"] == "PLC" and "re-established" in event["message"]
    ]
    assert len(recovered) == 1


def test_connecting_is_not_enough_a_read_must_succeed(qapp, wired) -> None:
    """A driver can open against a controller that will not answer these tags."""

    class OpensButSilent(FlakyPlcService):
        def connect(self) -> None:
            self.connect_calls += 1
            self._connected = True

        def read_cycle_state(self):
            self.read_calls += 1
            raise PlcError("No response for BatteryVision.Trigger")

    wired.plc = OpensButSilent()
    wired._plc_poll_failed("read timed out")
    drain(qapp)

    wired._attempt_plc_reconnect()
    drain(qapp)

    assert wired.plc.read_calls >= 1
    assert wired.health["plc"]["ok"] is False
    assert wired.plc_poll_timer.isActive() is False


def test_a_simulated_plc_is_never_reconnected(qapp, station) -> None:
    """Retrying a simulated backend would hide a defect behind a retry loop."""

    assert isinstance(station.plc, MockPlcService)

    station._plc_poll_failed("something went wrong in simulation")
    drain(qapp)

    assert station.plc_reconnect_timer.isActive() is False


def test_reconnection_never_changes_the_backend(qapp, wired) -> None:
    wired.plc.link_up = False
    wired._plc_poll_failed("read timed out")
    drain(qapp)
    wired.plc.link_up = True
    wired._attempt_plc_reconnect()
    drain(qapp)

    assert wired.plc_backend_active == "pycomm3"
    assert isinstance(wired.plc, FlakyPlcService)


def test_applying_settings_by_hand_supersedes_a_pending_reconnect(qapp, wired) -> None:
    wired.plc.link_up = False
    wired._plc_poll_failed("read timed out")
    drain(qapp)
    assert wired.plc_reconnect_timer.isActive() is True

    wired.cancel_plc_reconnect()

    assert wired.plc_reconnect_timer.isActive() is False
    assert wired._plc_reconnect_delay_ms == 0


def test_an_open_recipe_is_re_asserted_after_recovery(qapp, wired) -> None:
    """A reconnected controller has no memory of the Busy it was holding."""

    wired.begin_recipe_session()
    drain(qapp)
    wired.plc.link_up = False
    wired._plc_poll_failed("read timed out")
    drain(qapp)
    wired.plc.clear_result()

    wired.plc.link_up = True
    wired._attempt_plc_reconnect()
    drain(qapp)

    assert wired.plc.snapshot()["busy"] is True
