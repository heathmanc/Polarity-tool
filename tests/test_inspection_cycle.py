"""End-to-end inspection cycles through the real controller.

This is the path the station runs all day -- trigger, acquire a fresh frame,
grade it through the pipeline, write evidence, publish to the PLC -- and it was
the largest untested area of the controller. Existing coverage exercised the
vision pipeline directly and the controller's counters through a stand-in, so
nothing verified that the controller wires those together correctly.

Each cycle here runs against the bundled simulation camera and the seeded
demonstration recipe, so the assertions describe real graded results rather
than mocked dispositions. Several of them pin change-control invariants from
the README, which is noted per test.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from battery_inspector.config import AppConfig
from battery_inspector.controller import AppController
from battery_inspector.data import RecipeRepository
from battery_inspector.models import InspectionCycleState, InspectionDisposition
from battery_inspector.services.camera import CameraError

from conftest import ROOT, drain

ASSETS = ROOT / "battery_inspector" / "assets"
# The bundled pair the vision regressions already rely on: the good reference,
# and a demo battery whose markings are reversed relative to it.
GOOD_REFERENCE = ASSETS / "demo_reference_good.png"
REVERSED_BATTERY = ASSETS / "demo_battery.jpg"


def _build_controller(tmp_path: Path, *, seed: bool) -> AppController:
    root = tmp_path / "station"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    if seed:
        RecipeRepository(runtime / "battery_inspector.db").seed_demo_data(GOOD_REFERENCE)
    config = dataclasses.replace(
        AppConfig(),
        camera_backend="simulation",
        plc_backend="simulation",
        data_directory=str(runtime),
    )
    return AppController(root, config, resource_root=ROOT)


@pytest.fixture()
def station_controller(qapp, tmp_path):
    """A started station with the seeded, validated demonstration recipe."""

    instance = _build_controller(tmp_path, seed=True)
    instance.initialize()
    drain(qapp)
    yield instance
    instance.shutdown()
    qapp.processEvents()


@pytest.fixture()
def bare_controller(qapp, tmp_path):
    """A started station with no recipe at all."""

    instance = _build_controller(tmp_path, seed=False)
    instance.initialize()
    drain(qapp)
    yield instance
    instance.shutdown()
    qapp.processEvents()


def _run(qapp, controller: AppController, source: str = "MANUAL") -> None:
    assert controller.run_inspection(source) is True
    drain(qapp)


def _expect_pass(controller: AppController) -> None:
    """Grade the accepted reference image against itself."""

    controller.camera.image_path = GOOD_REFERENCE


# --- the station is ready --------------------------------------------------


def test_the_seeded_station_reports_itself_ready(station_controller) -> None:
    readiness = station_controller.inspection_readiness()

    assert readiness["ready"] is True
    assert readiness["issues"] == []
    assert readiness["recipe_has_reference"] is True
    assert station_controller.active_recipe.name == "GROUP31_XHD"


# --- a graded reject cycle -------------------------------------------------


def test_manual_cycle_rejects_the_reversed_demonstration_battery(qapp, station_controller) -> None:
    _run(qapp, station_controller)
    result = station_controller.last_inspection

    assert result.disposition is InspectionDisposition.REJECT
    assert result.reason == "POLARITY MARKINGS REVERSED"
    assert result.analysis_ready is True
    assert result.is_product_result is True


def test_a_graded_cycle_carries_its_frame_and_cycle_identity(qapp, station_controller) -> None:
    _run(qapp, station_controller)
    result = station_controller.last_inspection

    assert result.cycle_id
    assert result.frame_id
    assert result.frame_sequence >= 1
    assert result.captured_at_utc
    assert station_controller.cycle_status.state is InspectionCycleState.COMPLETE


def test_reject_evidence_is_written_and_recorded(qapp, station_controller) -> None:
    """Change-control invariant 5: non-PASS stays evidence-backed."""

    _run(qapp, station_controller)
    result = station_controller.last_inspection
    evidence = Path(result.evidence_directory)

    assert evidence.is_dir()
    assert (evidence / "manifest.json").is_file()
    assert station_controller.repository.inspection_summary()["fail_count"] == 1


def test_reject_manifest_describes_the_graded_cycle(qapp, station_controller) -> None:
    _run(qapp, station_controller)
    result = station_controller.last_inspection

    manifest = json.loads(
        (Path(result.evidence_directory) / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["cycle"]["cycle_id"] == result.cycle_id
    assert manifest["cycle"]["trigger_source"] == "MANUAL"
    assert manifest["result"]["disposition"] == result.disposition.value
    assert manifest["result"]["analysis_ready"] is True


# --- a graded pass cycle ---------------------------------------------------


def test_pass_cycle_grades_the_accepted_reference(qapp, station_controller) -> None:
    _expect_pass(station_controller)

    _run(qapp, station_controller)
    result = station_controller.last_inspection

    assert result.disposition is InspectionDisposition.PASS
    assert result.reason == "INSPECTION PASSED"


def test_pass_cycle_writes_no_evidence_and_no_history(qapp, station_controller) -> None:
    """Change-control invariant 4: production PASS remains memory-only."""

    _expect_pass(station_controller)

    _run(qapp, station_controller)
    result = station_controller.last_inspection

    assert result.evidence_directory == ""
    assert result.manifest_path == ""
    assert station_controller.repository.inspection_summary()["part_count"] == 0


def test_pass_cycle_still_moves_the_session_counters(qapp, station_controller) -> None:
    _expect_pass(station_controller)

    _run(qapp, station_controller)

    assert station_controller.counts_payload() == {
        "part_count": 1,
        "pass_count": 1,
        "fail_count": 0,
        "reject_rate": 0.0,
        "recent": [True],
    }


def test_pass_imagery_stays_available_in_memory_for_the_hmi(qapp, station_controller) -> None:
    _expect_pass(station_controller)

    _run(qapp, station_controller)
    result = station_controller.last_inspection

    # The HMI renders the latest PASS from RAM; only the filesystem is skipped.
    assert result.full_image is not None


# --- the PLC handshake -----------------------------------------------------


def test_plc_cycle_publishes_a_binary_pass(qapp, station_controller) -> None:
    """Change-control invariant 11: mutually exclusive binary Pass/Fail."""

    _expect_pass(station_controller)

    _run(qapp, station_controller, "PLC")

    assert station_controller.plc.last_result == {
        "passed": True,
        "fail": False,
        "busy": False,
        "complete": True,
    }


def test_plc_cycle_publishes_a_binary_fail(qapp, station_controller) -> None:
    _run(qapp, station_controller, "PLC")

    assert station_controller.plc.last_result == {
        "passed": False,
        "fail": True,
        "busy": False,
        "complete": True,
    }


def test_manual_and_plc_triggers_grade_identically(qapp, station_controller) -> None:
    """Change-control invariant 2: one pipeline behind every trigger path."""

    _run(qapp, station_controller, "MANUAL")
    manual = station_controller.last_inspection

    _run(qapp, station_controller, "PLC")
    plc = station_controller.last_inspection

    assert manual.disposition is plc.disposition
    assert manual.reason == plc.reason
    assert manual.trigger_source == "MANUAL"
    assert plc.trigger_source == "PLC"


def test_a_manual_cycle_leaves_the_plc_result_untouched(qapp, station_controller) -> None:
    """Only a PLC-triggered cycle owns the PLC result tags."""

    _run(qapp, station_controller, "MANUAL")

    assert station_controller.plc.last_result == {}


# --- acquisition faults ----------------------------------------------------


def test_a_camera_failure_faults_the_cycle_instead_of_grading(
    qapp, station_controller, monkeypatch
) -> None:
    """Change-control invariant 1: never grade a cached frame after a failure."""

    def refuse() -> None:
        raise CameraError("camera dropped the frame")

    monkeypatch.setattr(station_controller.camera, "capture", refuse)

    _run(qapp, station_controller)
    result = station_controller.last_inspection

    assert result.disposition is InspectionDisposition.SYSTEM_FAULT
    assert result.reason == "NO NEW CAMERA FRAME"
    assert result.analysis_ready is False
    assert station_controller.cycle_status.state is InspectionCycleState.FAULT


def test_a_faulted_cycle_does_not_move_production_counters(
    qapp, station_controller, monkeypatch
) -> None:
    def refuse() -> None:
        raise CameraError("camera dropped the frame")

    monkeypatch.setattr(station_controller.camera, "capture", refuse)

    _run(qapp, station_controller)

    assert station_controller.counts_payload()["part_count"] == 0


def test_a_faulted_cycle_still_produces_evidence(qapp, station_controller, monkeypatch) -> None:
    def refuse() -> None:
        raise CameraError("camera dropped the frame")

    monkeypatch.setattr(station_controller.camera, "capture", refuse)

    _run(qapp, station_controller)
    result = station_controller.last_inspection

    assert (Path(result.evidence_directory) / "manifest.json").is_file()


def test_a_stale_frame_is_refused(qapp, station_controller, monkeypatch) -> None:
    """Change-control invariant 1: a frame predating the request is not graded.

    CameraFrame.fresh is computed from the acquisition timestamps, so the frame
    is aged by moving its capture instant before the request instant rather than
    by overriding the property.
    """

    real_capture = station_controller.camera.capture

    def stale():
        frame = real_capture()
        aged = dataclasses.replace(
            frame,
            captured_monotonic_ns=frame.request_monotonic_ns - 1,
        )
        assert aged.fresh is False
        return aged

    monkeypatch.setattr(station_controller.camera, "capture", stale)

    _run(qapp, station_controller)
    result = station_controller.last_inspection

    assert result.disposition is InspectionDisposition.SYSTEM_FAULT
    assert result.reason == "NO NEW CAMERA FRAME"
    assert result.analysis_ready is False


# --- no recipe -------------------------------------------------------------


def test_a_station_without_a_recipe_reports_not_ready(qapp, bare_controller) -> None:
    """Change-control invariant 3: fail closed on uncertainty."""

    _run(qapp, bare_controller)
    result = bare_controller.last_inspection

    assert result.disposition is InspectionDisposition.NOT_READY
    assert result.reason == "NO ACTIVE RECIPE"
    assert "NO_ACTIVE_RECIPE" in result.readiness_issues
    assert bare_controller.counts_payload()["part_count"] == 0


# --- concurrency guards ----------------------------------------------------


def test_a_second_request_is_refused_while_a_cycle_is_in_flight(station_controller) -> None:
    station_controller._inspection_in_flight = True

    assert station_controller.run_inspection("MANUAL") is False


def test_a_plc_edge_arriving_during_a_cycle_is_held_rather_than_dropped(
    station_controller,
) -> None:
    """The polling service has already consumed the edge; losing it loses a part."""

    station_controller._inspection_in_flight = True

    assert station_controller.run_inspection("PLC") is False
    assert station_controller._pending_inspection_trigger_source == "PLC"


def test_a_manual_request_during_startup_is_refused(station_controller) -> None:
    station_controller._startup_in_flight = True

    assert station_controller.run_inspection("MANUAL") is False
    assert station_controller._pending_inspection_trigger_source is None


# --- repeated cycles -------------------------------------------------------


def test_consecutive_cycles_accumulate_session_yield(qapp, station_controller) -> None:
    _expect_pass(station_controller)
    _run(qapp, station_controller)
    _run(qapp, station_controller)
    station_controller.camera.image_path = REVERSED_BATTERY
    _run(qapp, station_controller)

    counts = station_controller.counts_payload()

    assert counts["part_count"] == 3
    assert counts["pass_count"] == 2
    assert counts["fail_count"] == 1
    assert counts["recent"] == [True, True, False]
    assert counts["reject_rate"] == pytest.approx(100 / 3)


def test_each_cycle_acquires_a_new_frame(qapp, station_controller) -> None:
    _run(qapp, station_controller)
    first = station_controller.last_inspection

    _run(qapp, station_controller)
    second = station_controller.last_inspection

    assert first.frame_id != second.frame_id
    assert second.frame_sequence > first.frame_sequence
    assert first.cycle_id != second.cycle_id


# --- last-resort failure handling ------------------------------------------
#
# These run on the Qt main thread, unlike the cycle body, and cover what the
# station does when the worker itself fails rather than when a part fails.


def test_a_worker_failure_becomes_an_evidence_backed_system_fault(
    qapp, station_controller
) -> None:
    station_controller._begin_cycle_status("MANUAL")

    station_controller._inspection_task_failed("worker died mid-cycle")
    qapp.processEvents()

    result = station_controller.last_inspection
    manifest = json.loads(
        (Path(result.evidence_directory) / "manifest.json").read_text(encoding="utf-8")
    )

    assert result.disposition is InspectionDisposition.SYSTEM_FAULT
    assert result.reason == "INSPECTION WORKER FAILURE"
    assert result.analysis_ready is False
    # The diagnostic has to survive into the evidence package, not just the log.
    assert manifest["fault_details"] == "worker died mid-cycle"
    assert station_controller.cycle_status.state is InspectionCycleState.FAULT


def test_a_worker_failure_does_not_move_production_counters(qapp, station_controller) -> None:
    station_controller._begin_cycle_status("PLC")

    station_controller._inspection_task_failed("worker died mid-cycle")
    qapp.processEvents()

    assert station_controller.counts_payload()["part_count"] == 0


def test_an_invalid_worker_payload_is_treated_as_a_failure(qapp, station_controller) -> None:
    station_controller._begin_cycle_status("MANUAL")

    station_controller._inspection_completed("not an inspection result")
    qapp.processEvents()

    assert station_controller.last_inspection.disposition is InspectionDisposition.SYSTEM_FAULT


def test_a_held_plc_edge_runs_once_the_station_is_free(qapp, station_controller) -> None:
    """The edge held during a busy cycle must still produce a part."""

    station_controller._pending_inspection_trigger_source = "PLC"

    station_controller._resume_queued_work()
    drain(qapp)

    assert station_controller._pending_inspection_trigger_source is None
    assert station_controller.last_inspection is not None
    assert station_controller.last_inspection.trigger_source == "PLC"


def test_a_held_edge_stays_held_while_the_station_is_still_busy(station_controller) -> None:
    station_controller._pending_inspection_trigger_source = "PLC"
    station_controller._inspection_in_flight = True

    station_controller._resume_queued_work()

    assert station_controller._pending_inspection_trigger_source == "PLC"
