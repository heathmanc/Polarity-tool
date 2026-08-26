"""Runtime tests against a real AppController instance.

`test_controller_counters.py` covers the session-counter reset by calling one
unbound method against a duck-typed stand-in. That style verifies a single
algorithm but never constructs the controller, so startup, the PLC state
handler, recipe persistence, and the configuration path -- the parts that own
station behavior -- were unexercised.

These tests build the controller against a temporary station root with both
backends pinned to simulation, so no camera, PLC, or station data directory is
touched.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import cv2
import numpy as np
import pytest

from battery_inspector.evidence import sha256_file
from battery_inspector.models import (
    InspectionDisposition,
    InspectionResult,
    Marking,
    NormalizedRect,
    Recipe,
    ReferenceCapture,
    TerminalRecipe,
    TerminalRole,
)

from conftest import drain, mark_validated


def _reference_capture(directory, name: str = "reference.png") -> ReferenceCapture:
    """Write a real image and describe it exactly as the capture path would.

    persist_recipe_reference re-hashes the file and refuses a mismatch, so the
    fixture has to carry the true digest rather than a placeholder.
    """

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    image = np.full((120, 240, 3), 40, dtype=np.uint8)
    cv2.imwrite(str(path), image)
    return ReferenceCapture(
        capture_id="capture-1",
        path=str(path),
        sha256=sha256_file(path),
        captured_at_utc=datetime.now(timezone.utc).isoformat(),
        width_px=240,
        height_px=120,
    )


def _recipe(reference: ReferenceCapture, *, number: int = 0, name: str = "MODEL_A") -> Recipe:
    return Recipe.new(
        name=name,
        recipe_number=number,
        part_number="PN-1",
        description="controller runtime fixture",
        created_by="test",
        battery_roi=NormalizedRect(0.0, 0.0, 1.0, 1.0),
        terminals=[
            TerminalRecipe(
                key="negative",
                name="Negative Terminal",
                role=TerminalRole.NEGATIVE,
                search_roi=NormalizedRect(0.0, 0.0, 0.5, 1.0),
                marking_roi=NormalizedRect(0.1, 0.1, 0.3, 0.3),
                expected_marking=Marking.MINUS,
                red_ring_required=False,
            ),
            TerminalRecipe(
                key="positive",
                name="Positive Terminal",
                role=TerminalRole.POSITIVE,
                search_roi=NormalizedRect(0.5, 0.0, 0.5, 1.0),
                marking_roi=NormalizedRect(0.6, 0.1, 0.3, 0.3),
                expected_marking=Marking.PLUS,
                red_ring_required=True,
            ),
        ],
        reference_image=reference,
    )


def _result(*, passed: bool, analysis_ready: bool = True) -> InspectionResult:
    return InspectionResult.create(
        recipe=None,
        disposition=(
            InspectionDisposition.PASS if passed else InspectionDisposition.REJECT
        ),
        reason="PASS" if passed else "Polarity reversed",
        duration_ms=12,
        trigger_source="MANUAL",
        image_quality="OK",
        full_image_path="/evidence/frame-1.png",
        terminals=[],
        frame_id="frame-1",
        analysis_ready=analysis_ready,
    )


# --- construction and startup ---------------------------------------------


def test_controller_constructs_against_an_empty_station(controller, station) -> None:
    root, _config = station

    assert controller.project_root == root
    assert controller.active_recipe is None
    assert controller.part_count == 0
    assert controller.data_directory.is_dir()
    assert controller.busy is False


def test_startup_connects_both_simulation_backends(qapp, controller) -> None:
    controller.initialize()
    drain(qapp)

    assert controller.camera_backend_active == "simulation"
    assert controller.plc_backend_active == "simulation"
    assert controller.health["camera"]["text"] == "SIMULATION"
    assert controller.health["plc"]["text"] == "SIMULATION"
    assert controller.busy is False


def test_startup_records_a_system_audit_event(qapp, controller) -> None:
    controller.initialize()
    drain(qapp)

    assert any(event["category"] == "SYSTEM" for event in controller.audit_events())


def test_readiness_reports_the_missing_recipe_on_a_new_station(controller) -> None:
    readiness = controller.inspection_readiness()

    assert readiness["ready"] is False
    assert "NO_ACTIVE_RECIPE" in readiness["issues"]
    assert readiness["recipe_has_reference"] is False


# --- production counters ---------------------------------------------------


def test_reject_result_increments_counters_and_is_persisted(controller) -> None:
    controller._accept_inspection(_result(passed=False), increment_counts=True)

    assert controller.part_count == 1
    assert controller.fail_count == 1
    assert controller.pass_count == 0
    assert list(controller.recent_results) == [False]
    assert controller.repository.inspection_summary()["fail_count"] == 1


def test_pass_result_increments_counters_without_persisting_history(controller) -> None:
    """PASS traceability is deliberately session-only; see STORAGE_POLICY.md."""

    controller._accept_inspection(_result(passed=True), increment_counts=True)

    assert controller.part_count == 1
    assert controller.pass_count == 1
    assert controller.repository.inspection_summary()["part_count"] == 0


def test_results_without_analysis_do_not_move_production_counters(controller) -> None:
    controller._accept_inspection(
        _result(passed=False, analysis_ready=False), increment_counts=True
    )

    assert controller.part_count == 0
    assert controller.fail_count == 0
    assert controller.last_inspection is not None


def test_reject_rate_is_derived_from_the_session_counters(controller) -> None:
    for _ in range(3):
        controller._accept_inspection(_result(passed=True), increment_counts=True)
    controller._accept_inspection(_result(passed=False), increment_counts=True)

    assert controller.reject_rate == pytest.approx(25.0)
    assert controller.counts_payload()["part_count"] == 4


def test_counter_reset_clears_the_session_without_deleting_inspections(controller) -> None:
    controller._accept_inspection(_result(passed=False), increment_counts=True)

    assert controller.reset_production_counters() is True
    assert controller.part_count == 0
    assert controller.counts_payload()["recent"] == []
    # Retained failure evidence is independent of the session counters.
    assert controller.repository.inspection_summary()["fail_count"] == 1


# --- PLC cycle handling ----------------------------------------------------


def test_plc_trigger_edge_starts_exactly_one_inspection(controller, monkeypatch) -> None:
    # About edge latching, not product identity: this station's recipe source
    # is its own selection, so the trigger has something to grade against
    # without the state carrying a selector value.
    controller.config.plc_recipe_source = "station"
    started: list[str] = []
    monkeypatch.setattr(
        controller, "run_inspection", lambda source="MANUAL": started.append(source)
    )

    controller._handle_plc_state({"trigger": False})
    controller._handle_plc_state({"trigger": True})
    controller._handle_plc_state({"trigger": True})
    controller._handle_plc_state({"trigger": True})

    assert started == ["PLC"]


def test_plc_trigger_rearms_after_the_tag_drops(controller, monkeypatch) -> None:
    controller.config.plc_recipe_source = "station"
    started: list[str] = []
    monkeypatch.setattr(
        controller, "run_inspection", lambda source="MANUAL": started.append(source)
    )

    controller._handle_plc_state({"trigger": True})
    controller._handle_plc_state({"trigger": False})
    controller._handle_plc_state({"trigger": True})

    assert started == ["PLC", "PLC"]


def test_plc_bypass_readback_is_mirrored_and_logged_once(controller) -> None:
    controller._handle_plc_state({"trigger": False, "bypass": True})
    controller._handle_plc_state({"trigger": False, "bypass": True})

    bypass_events = [
        event for event in controller.audit_events() if event["category"] == "BYPASS"
    ]

    assert controller.bypass_active is True
    assert len(bypass_events) == 1


def test_recipe_number_mismatch_blocks_the_trigger_and_is_logged_once(
    qapp, controller, tmp_path, monkeypatch
) -> None:
    reference = _reference_capture(tmp_path / "ref")
    saved = controller.save_recipe(_recipe(reference, number=7), activate=False)
    controller.active_recipe = saved
    started: list[str] = []
    monkeypatch.setattr(
        controller, "run_inspection", lambda source="MANUAL": started.append(source)
    )

    for _ in range(3):
        controller._handle_plc_state(
            {"trigger": True, "recipe_selector": "number", "recipe_number": 9}
        )

    mismatch_events = [
        event
        for event in controller.audit_events()
        if event["category"] == "PLC" and "requested recipe" in event["message"]
    ]

    assert started == []
    assert len(mismatch_events) == 1


def test_matching_recipe_number_allows_the_trigger(
    qapp, controller, tmp_path, monkeypatch
) -> None:
    """The PLC names 7, and a validated revision of 7 exists to grade against."""

    reference = _reference_capture(tmp_path / "ref")
    controller.active_recipe = controller.save_recipe(
        mark_validated(_recipe(reference, number=7))
    )
    started: list[str] = []
    monkeypatch.setattr(
        controller, "run_inspection", lambda source="MANUAL": started.append(source)
    )

    controller._handle_plc_state(
        {"trigger": True, "recipe_selector": "number", "recipe_number": 7}
    )

    assert started == ["PLC"]


def test_plc_simulation_state_reports_the_mock_driver(qapp, controller) -> None:
    controller.initialize()
    drain(qapp)

    state = controller.plc_simulation_state()

    assert controller.plc_simulation_active is True
    assert state["trigger"] is False
    assert "recipe_selector" in state


# --- recipe persistence ----------------------------------------------------


def test_saving_a_recipe_assigns_the_next_free_number(controller, tmp_path) -> None:
    reference = _reference_capture(tmp_path / "ref")

    saved = controller.save_recipe(_recipe(reference, number=0))

    assert saved.recipe_number == 1
    assert controller.next_recipe_number() == 2
    assert [item.name for item in controller.list_recipes()] == ["MODEL_A"]


def test_duplicate_recipe_names_are_refused(controller, tmp_path) -> None:
    controller.save_recipe(_recipe(_reference_capture(tmp_path / "a"), number=1))

    with pytest.raises(ValueError, match="already exists"):
        controller.save_recipe(
            _recipe(_reference_capture(tmp_path / "b", "b.png"), number=2)
        )


def test_duplicate_recipe_numbers_are_refused(controller, tmp_path) -> None:
    controller.save_recipe(_recipe(_reference_capture(tmp_path / "a"), number=4))

    with pytest.raises(ValueError, match="already assigned"):
        controller.save_recipe(
            _recipe(
                _reference_capture(tmp_path / "b", "b.png"), number=4, name="MODEL_B"
            )
        )


def test_saving_without_a_reference_image_is_refused(controller) -> None:
    with pytest.raises(ValueError, match="reference image is required"):
        controller.save_recipe(_recipe(None, number=1))


def test_an_unvalidated_recipe_cannot_be_activated(controller, tmp_path) -> None:
    saved = controller.save_recipe(_recipe(_reference_capture(tmp_path / "ref")))

    with pytest.raises(ValueError, match="guided validation"):
        controller.activate_recipe(saved)


def test_a_saved_recipe_can_be_deleted(controller, tmp_path) -> None:
    saved = controller.save_recipe(_recipe(_reference_capture(tmp_path / "ref")))

    controller.delete_recipe(saved)

    assert controller.list_recipes() == []


def test_the_active_recipe_cannot_be_deleted(controller, tmp_path) -> None:
    saved = controller.save_recipe(_recipe(_reference_capture(tmp_path / "ref")))
    controller.active_recipe = saved

    with pytest.raises(ValueError, match="active recipe cannot be deleted"):
        controller.delete_recipe(saved)


# --- configuration ---------------------------------------------------------


def test_updating_configuration_normalizes_and_announces_it(controller) -> None:
    received: list[object] = []
    controller.configuration_changed.connect(received.append)

    controller.update_configuration(
        dataclasses.replace(controller.config, operator_name="A. Technician")
    )

    assert controller.config.operator_name == "A. Technician"
    assert received and received[-1].operator_name == "A. Technician"


def test_shutdown_stops_the_plc_timers(controller) -> None:
    controller.plc_poll_timer.start()
    controller.plc_heartbeat_timer.start()

    controller.shutdown()

    assert controller.plc_poll_timer.isActive() is False
    assert controller.plc_heartbeat_timer.isActive() is False


# --- staged captures are swept at startup -----------------------------------


def test_startup_removes_abandoned_staged_captures(qapp, controller, monkeypatch) -> None:
    """The station must not accumulate reference captures for its whole life."""

    import os
    from datetime import timedelta

    staging = controller.ml_training_store.staging_root
    recipe_staging = controller.data_directory / "recipe_staging"
    for directory in (staging, recipe_staging):
        directory.mkdir(parents=True, exist_ok=True)
        stale = directory / "reference-abandoned.png"
        stale.write_bytes(b"x" * 4096)
        when = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        os.utime(stale, (when, when))

    controller.initialize()
    drain(qapp)

    assert not (staging / "reference-abandoned.png").exists()
    assert not (recipe_staging / "reference-abandoned.png").exists()


def test_startup_keeps_a_recent_staged_capture(qapp, controller) -> None:
    staging = controller.ml_training_store.staging_root
    staging.mkdir(parents=True, exist_ok=True)
    current = staging / "reference-in-progress.png"
    current.write_bytes(b"x" * 4096)

    controller.initialize()
    drain(qapp)

    assert current.exists()


def test_a_sweep_that_removes_nothing_is_not_announced(qapp, controller) -> None:
    controller.initialize()
    drain(qapp)

    messages = [event["message"] for event in controller.audit_events()]
    assert not [m for m in messages if "abandoned reference capture" in m]
