from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RunStatePresentation:
    tone: str
    title: str
    subtitle: str


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def derive_run_state(
    *,
    busy: bool,
    busy_reason: str,
    system_ok: bool,
    plc_simulation: bool,
    last_result_passed: bool | None,
    system_text: str = "",
    cycle_state: Any = None,
    cycle_message: str = "",
    last_disposition: Any = None,
) -> RunStatePresentation:
    """Return the single authoritative HMI header state.

    ``busy`` covers startup/settings work. ``cycle_state`` covers the inspection
    state machine. Keeping the two inputs separate prevents a completed camera or
    PLC task from leaving the header on INSPECTING and prevents a deliberately
    fail-closed vision state from being presented as a generic hardware fault.
    """

    cycle = _enum_value(cycle_state)
    active_cycles = {"acquiring", "locating", "inspecting", "saving"}
    if cycle in active_cycles:
        # An active inspection is a normal operating state, not an alarm.
        return RunStatePresentation("info", "BUSY", cycle.replace("_", " ").upper())

    if busy:
        reason = (busy_reason or "WORKING").strip().upper()
        return RunStatePresentation("info", "BUSY", reason[:28])

    disposition = _enum_value(last_disposition)
    normalized_system_text = str(system_text or "").strip().upper()
    if cycle == "not_ready" or disposition == "not_ready" or normalized_system_text == "NOT READY":
        detail = (cycle_message or "INSPECTION ENGINE").strip().upper()
        if detail.startswith("INSPECTION NOT READY"):
            detail = "LOCATOR / CLASSIFIER REQUIRED"
        return RunStatePresentation("warning", "NOT READY", detail[:36])

    if cycle == "fault" or disposition == "system_fault" or not system_ok:
        return RunStatePresentation("bad", "DEGRADED", "CHECK STATUS")

    if plc_simulation:
        if disposition == "reject" or last_result_passed is False:
            return RunStatePresentation("bad", "READY", "LAST REJECT / PLC SIM")
        if disposition == "pass" or last_result_passed is True:
            # Simulation remains an amber commissioning state even after a pass.
            return RunStatePresentation("warning", "READY", "LAST PASS / PLC SIM")
        return RunStatePresentation("warning", "READY", "PLC SIMULATION")

    if disposition == "reject" or last_result_passed is False:
        return RunStatePresentation("bad", "READY", "LAST RESULT: REJECT")
    if disposition == "pass" or last_result_passed is True:
        return RunStatePresentation("good", "READY", "LAST RESULT: PASS")
    # Healthy steady-state operation is intentionally neutral.
    return RunStatePresentation("neutral", "RUNNING", "AUTO MODE")
