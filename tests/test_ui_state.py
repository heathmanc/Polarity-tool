from battery_inspector.models import InspectionCycleState, InspectionDisposition
from battery_inspector.ui_state import derive_run_state


def test_completed_reject_never_leaves_header_inspecting() -> None:
    state = derive_run_state(
        busy=False,
        busy_reason="",
        system_ok=True,
        plc_simulation=True,
        last_result_passed=False,
        cycle_state=InspectionCycleState.COMPLETE,
        last_disposition=InspectionDisposition.REJECT,
    )

    assert state.title == "READY"
    assert state.tone == "bad"
    assert "REJECT" in state.subtitle
    assert "INSPECTING" not in state.subtitle


def test_fault_after_inspection_is_degraded_not_busy() -> None:
    state = derive_run_state(
        busy=False,
        busy_reason="",
        system_ok=False,
        plc_simulation=False,
        last_result_passed=None,
        cycle_state=InspectionCycleState.FAULT,
        last_disposition=InspectionDisposition.SYSTEM_FAULT,
    )

    assert state.title == "DEGRADED"
    assert state.subtitle == "CHECK STATUS"


def test_busy_reason_is_visible_during_non_cycle_work() -> None:
    state = derive_run_state(
        busy=True,
        busy_reason="CONFIGURING CAMERA",
        system_ok=True,
        plc_simulation=False,
        last_result_passed=None,
    )

    assert state.title == "BUSY"
    assert state.tone == "info"
    assert state.subtitle == "CONFIGURING CAMERA"


def test_cycle_state_overrides_generic_busy_reason() -> None:
    state = derive_run_state(
        busy=True,
        busy_reason="INSPECTION",
        system_ok=False,
        plc_simulation=True,
        last_result_passed=None,
        cycle_state=InspectionCycleState.ACQUIRING,
        cycle_message="Waiting for a fresh camera frame",
    )

    assert state.title == "BUSY"
    assert state.tone == "info"
    assert state.subtitle == "ACQUIRING"


def test_not_ready_is_not_presented_as_pass_or_generic_fault() -> None:
    state = derive_run_state(
        busy=False,
        busy_reason="",
        system_ok=False,
        system_text="NOT READY",
        plc_simulation=True,
        last_result_passed=None,
        cycle_state=InspectionCycleState.NOT_READY,
        cycle_message="Inspection not ready — classifier required",
        last_disposition=InspectionDisposition.NOT_READY,
    )

    assert state.title == "NOT READY"
    assert "CLASSIFIER" in state.subtitle
    assert "PASS" not in state.subtitle


def test_healthy_steady_state_is_neutral_not_continuously_green() -> None:
    state = derive_run_state(
        busy=False,
        busy_reason="",
        system_ok=True,
        plc_simulation=False,
        last_result_passed=None,
    )

    assert state.title == "RUNNING"
    assert state.subtitle == "AUTO MODE"
    assert state.tone == "neutral"


def test_completed_pass_is_the_explicit_green_state() -> None:
    state = derive_run_state(
        busy=False,
        busy_reason="",
        system_ok=True,
        plc_simulation=False,
        last_result_passed=True,
        cycle_state=InspectionCycleState.COMPLETE,
        last_disposition=InspectionDisposition.PASS,
    )

    assert state.title == "READY"
    assert state.subtitle == "LAST RESULT: PASS"
    assert state.tone == "good"
