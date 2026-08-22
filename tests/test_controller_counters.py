from __future__ import annotations

from collections import deque
from types import SimpleNamespace

from battery_inspector.controller import AppController


class _SignalRecorder:
    def __init__(self) -> None:
        self.payloads: list[object] = []

    def emit(self, payload: object) -> None:
        self.payloads.append(payload)


def _controller_double(*, busy: bool) -> SimpleNamespace:
    signal = _SignalRecorder()
    controller = SimpleNamespace(
        busy=busy,
        part_count=12,
        pass_count=10,
        fail_count=2,
        recent_results=deque([True, False, True], maxlen=13),
        counts_changed=signal,
    )
    controller.counts_payload = lambda: {
        "part_count": controller.part_count,
        "pass_count": controller.pass_count,
        "fail_count": controller.fail_count,
        "reject_rate": (
            controller.fail_count / controller.part_count * 100.0
            if controller.part_count
            else 0.0
        ),
        "recent": list(controller.recent_results),
    }
    return controller


def test_session_production_counters_reset_without_touching_other_state() -> None:
    controller = _controller_double(busy=False)

    accepted = AppController.reset_production_counters(controller)

    assert accepted is True
    assert controller.part_count == 0
    assert controller.pass_count == 0
    assert controller.fail_count == 0
    assert list(controller.recent_results) == []
    assert controller.counts_changed.payloads == [
        {
            "part_count": 0,
            "pass_count": 0,
            "fail_count": 0,
            "reject_rate": 0.0,
            "recent": [],
        }
    ]


def test_production_counter_reset_is_refused_while_station_is_busy() -> None:
    controller = _controller_double(busy=True)

    accepted = AppController.reset_production_counters(controller)

    assert accepted is False
    assert controller.part_count == 12
    assert controller.pass_count == 10
    assert controller.fail_count == 2
    assert list(controller.recent_results) == [True, False, True]
    assert controller.counts_changed.payloads == []
