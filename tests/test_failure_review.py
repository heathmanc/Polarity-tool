"""Walking up to the station and dealing with what rejected.

Every non-PASS product cycle already wrote a row and an evidence folder; none
of it was reachable without a file browser. These tests hold the parts of the
review flow that are safety- or record-relevant rather than cosmetic:

* clearing can only ever delete production failure evidence, whatever it is
  handed;
* a failure held for review survives retention;
* a crop sent to training is labelled by the technician, never by the model
  that just got it wrong.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from battery_inspector.evidence import (
    FailureRetentionPolicy,
    apply_failure_retention,
    remove_failure_evidence,
)
from battery_inspector.data.repository import (
    REVIEW_NEW,
    REVIEW_REVIEWED,
    REVIEW_TRAINING,
)


def _evidence_cycle(
    root: Path,
    inspection_id: str,
    *,
    disposition: str = "reject",
    when: datetime | None = None,
) -> Path:
    moment = when or datetime.now(timezone.utc)
    directory = root / moment.strftime("%Y%m%d") / inspection_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "result": {
                    "disposition": disposition,
                    "timestamp_utc": moment.isoformat(),
                    "inspection_id": inspection_id,
                }
            }
        ),
        encoding="utf-8",
    )
    image = np.full((32, 32, 3), 120, dtype=np.uint8)
    cv2.imwrite(str(directory / "full.png"), image)
    return directory


def _record(controller, inspection_id: str, directory: Path, **overrides) -> dict:
    payload = {
        "inspection_id": inspection_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "recipe_id": "RECIPE-1",
        "recipe_name": "GROUP31",
        "disposition": "reject",
        "reason": "Negative terminal marking mismatch",
        "evidence_directory": str(directory),
    }
    payload.update(overrides)
    controller.repository.save_inspection(payload)
    return payload


# --- listing and triage -----------------------------------------------------


def test_retained_failures_are_listed_newest_first(qapp, controller, tmp_path) -> None:
    root = tmp_path / "inspections"
    older = datetime.now(timezone.utc) - timedelta(hours=2)
    _record(
        controller,
        "OLD",
        _evidence_cycle(root, "OLD", when=older),
        timestamp_utc=older.isoformat(),
    )
    _record(controller, "NEW", _evidence_cycle(root, "NEW"))

    failures = controller.list_failures()

    assert [item["inspection_id"] for item in failures] == ["NEW", "OLD"]
    assert all(item["review_state"] == REVIEW_NEW for item in failures)


def test_a_pass_never_appears_in_the_review_list(qapp, controller, tmp_path) -> None:
    """Production PASS is memory-only. The page cannot show what is not kept."""

    _record(
        controller,
        "PASSED",
        _evidence_cycle(tmp_path / "inspections", "PASSED", disposition="pass"),
        disposition="pass",
    )

    assert controller.list_failures() == []


def test_reviewing_records_who_and_when(qapp, controller, tmp_path) -> None:
    directory = _evidence_cycle(tmp_path / "inspections", "ONE")
    _record(controller, "ONE", directory)

    controller.mark_failures_reviewed(["ONE"])

    entry = controller.list_failures()[0]
    assert entry["review_state"] == REVIEW_REVIEWED
    assert entry["reviewed_by"] == controller.config.operator_name
    assert entry["reviewed_at_utc"]


def test_counts_drive_the_header(qapp, controller, tmp_path) -> None:
    root = tmp_path / "inspections"
    _record(controller, "A", _evidence_cycle(root, "A"))
    _record(controller, "B", _evidence_cycle(root, "B"))
    controller.mark_failures_reviewed(["B"])

    counts = controller.failure_counts()

    assert counts["total"] == 2
    assert counts[REVIEW_NEW] == 1
    assert counts[REVIEW_REVIEWED] == 1


# --- retention interplay ----------------------------------------------------


def test_a_kept_failure_survives_the_age_cutoff(qapp, controller, tmp_path) -> None:
    """The interesting failure is the one most likely to age out first."""

    root = tmp_path / "inspections"
    stale = datetime.now(timezone.utc) - timedelta(days=90)
    kept = _evidence_cycle(root, "KEPT", when=stale)
    dropped = _evidence_cycle(root, "DROPPED", when=stale)
    _record(controller, "KEPT", kept, timestamp_utc=stale.isoformat())
    _record(controller, "DROPPED", dropped, timestamp_utc=stale.isoformat())
    controller.set_failures_kept(["KEPT"], True)

    apply_failure_retention(
        root,
        FailureRetentionPolicy(max_age_days=30, max_bytes=0),
        protected_directories=controller.repository.protected_evidence_directories(),
    )

    assert kept.is_dir()
    assert not dropped.is_dir()


def test_protection_never_saves_pass_evidence(qapp, controller, tmp_path) -> None:
    """PASS is memory-only by policy; no flag may override that."""

    root = tmp_path / "inspections"
    passing = _evidence_cycle(root, "PASSED", disposition="pass")

    apply_failure_retention(
        root,
        FailureRetentionPolicy(max_age_days=0, max_bytes=0),
        protected_directories=[passing],
    )

    assert not passing.is_dir()


# --- clearing ---------------------------------------------------------------


def test_clearing_removes_the_evidence_and_the_row(qapp, controller, tmp_path) -> None:
    directory = _evidence_cycle(tmp_path / "inspections", "ONE")
    _record(controller, "ONE", directory)
    # The controller clears under its own data directory.
    target = controller.data_directory / "inspections"
    target.mkdir(parents=True, exist_ok=True)
    moved = _evidence_cycle(target, "TWO")
    _record(controller, "TWO", moved)

    summary = controller.clear_failures(controller.list_failures())

    assert not moved.is_dir()
    assert summary["rows_removed"] == 2
    assert controller.list_failures() == []


def test_clearing_refuses_a_directory_that_is_not_failure_evidence(tmp_path) -> None:
    """The scoping rule, tested directly: a caller cannot aim this anywhere."""

    root = tmp_path / "inspections"
    root.mkdir(parents=True)
    recipe_assets = tmp_path / "recipes" / "GROUP31"
    recipe_assets.mkdir(parents=True)
    (recipe_assets / "reference.png").write_bytes(b"a-reference")

    summary = remove_failure_evidence(root, [recipe_assets, tmp_path, Path("/")])

    assert summary["removed"] == 0
    assert recipe_assets.is_dir()
    assert (recipe_assets / "reference.png").is_file()


def test_a_directory_outside_the_evidence_root_is_never_removed(tmp_path) -> None:
    """Same shape as a cycle directory, wrong root. Scoping is by location too."""

    root = tmp_path / "inspections"
    root.mkdir(parents=True)
    elsewhere = _evidence_cycle(tmp_path / "somewhere_else", "LOOKS_REAL")

    summary = remove_failure_evidence(root, [elsewhere])

    assert summary["removed"] == 0
    assert elsewhere.is_dir()


# --- sending a rejected part's crops to training ----------------------------


def _failure_with_frame(controller, tmp_path, *, detected: str = "plus") -> dict:
    """A stored reject carrying the full frame and terminal geometry."""

    directory = _evidence_cycle(controller.data_directory / "inspections", "FAIL-1")
    frame = directory / "full.png"
    cv2.imwrite(str(frame), np.full((400, 600, 3), 90, dtype=np.uint8))
    _record(
        controller,
        "FAIL-1",
        directory,
        full_image_path=str(frame),
        terminals=[
            {
                "terminal_key": "negative",
                "terminal_name": "Negative Terminal",
                "detected_marking": detected,
                "expected_marking": "minus",
                "terminal_polygon": [
                    [0.30, 0.30],
                    [0.55, 0.30],
                    [0.55, 0.62],
                    [0.30, 0.62],
                ],
            }
        ],
    )
    return controller.list_failures()[0]


def test_a_crop_from_a_reject_reaches_the_training_set(qapp, controller, tmp_path) -> None:
    record = _failure_with_frame(controller, tmp_path)
    before = controller.ml_training_store.counts()

    result = controller.send_failure_to_training(record, {"negative": "minus"})

    after = controller.ml_training_store.counts()
    assert result["added"] == 1
    assert after.get("minus", 0) == before.get("minus", 0) + 1


def test_the_technician_labels_it_not_the_model(qapp, controller, tmp_path) -> None:
    """The whole point of the flow.

    A rejected part is exactly where the classifier may have been wrong. The
    label that reaches the training set is the one the technician chose, so a
    crop the model called PLUS is stored as MINUS when that is what is stamped.
    """

    record = _failure_with_frame(controller, tmp_path, detected="plus")

    controller.send_failure_to_training(record, {"negative": "minus"})

    stored = controller.ml_training_store.records()
    added = [item for item in stored if "failure_review" in item.collection_tag]
    assert len(added) == 1
    assert added[0].label == "minus"


def test_a_terminal_left_unlabelled_is_not_sent(qapp, controller, tmp_path) -> None:
    record = _failure_with_frame(controller, tmp_path)

    with pytest.raises(ValueError, match="true class"):
        controller.send_failure_to_training(record, {"negative": ""})


def test_sending_marks_the_failure_as_gone_to_training(qapp, controller, tmp_path) -> None:
    record = _failure_with_frame(controller, tmp_path)

    controller.send_failure_to_training(record, {"negative": "minus"})

    entry = controller.list_failures()[0]
    assert entry["review_state"] == REVIEW_TRAINING
    assert entry["training_at_utc"]


def test_a_failure_whose_frame_is_gone_says_so(qapp, controller, tmp_path) -> None:
    """Retention may have taken the image while the row survives."""

    record = _failure_with_frame(controller, tmp_path)
    Path(record["payload"]["full_image_path"]).unlink()

    with pytest.raises(ValueError, match="no longer on the station"):
        controller.send_failure_to_training(record, {"negative": "minus"})


# --- export -----------------------------------------------------------------


def test_exported_failures_carry_their_evidence_and_an_index(
    qapp, controller, tmp_path
) -> None:
    import zipfile

    record = _failure_with_frame(controller, tmp_path)
    destination = tmp_path / "failures.zip"

    result = controller.export_failures([record], destination, description="shift 2")

    with zipfile.ZipFile(result["path"]) as archive:
        names = archive.namelist()
    assert "pole_position_failure_export.json" in names
    assert any(name.startswith("failures/FAIL-1/") for name in names)
    assert result["manifest"]["description"] == "shift 2"
    assert controller.list_failures()[0]["exported_at_utc"]


def test_exporting_nothing_is_refused(qapp, controller) -> None:
    from battery_inspector.package_transfer import PackageTransferError

    with pytest.raises(PackageTransferError, match="No failures"):
        controller.export_failures([], Path("unused.zip"))


# --- the page ---------------------------------------------------------------


def _page(qapp, controller):
    from battery_inspector.ui.pages.failure_review import FailureReviewPage

    page = FailureReviewPage(controller)
    qapp.processEvents()
    return page


def _select_all_rows(page) -> None:
    page.table.selectAll()


def test_the_queue_lists_what_rejected(qapp, controller, tmp_path) -> None:
    root = controller.data_directory / "inspections"
    _record(controller, "A", _evidence_cycle(root, "A"))
    _record(controller, "B", _evidence_cycle(root, "B"))

    page = _page(qapp, controller)

    assert page.table.rowCount() == 2
    assert "2 retained" in page.counts.text()


def test_the_age_filter_narrows_the_queue(qapp, controller, tmp_path) -> None:
    root = controller.data_directory / "inspections"
    stale = datetime.now(timezone.utc) - timedelta(days=20)
    _record(
        controller,
        "OLD",
        _evidence_cycle(root, "OLD", when=stale),
        timestamp_utc=stale.isoformat(),
    )
    _record(controller, "NEW", _evidence_cycle(root, "NEW"))

    page = _page(qapp, controller)
    assert page.table.rowCount() == 1  # default filter is the last 7 days

    page.age_filter.setCurrentIndex(page.age_filter.findData(30))
    qapp.processEvents()

    assert page.table.rowCount() == 2


def test_opening_a_failure_emits_the_stored_result(qapp, controller, tmp_path) -> None:
    """The reviewer sees what the operator saw, rendered by the same widgets."""

    from battery_inspector.models import InspectionResult

    _failure_with_frame(controller, tmp_path)
    page = _page(qapp, controller)
    seen: list[InspectionResult] = []
    page.inspection_selected.connect(seen.append)

    _select_all_rows(page)
    page.open_selected()

    assert len(seen) == 1
    assert seen[0].inspection_id == "FAIL-1"
    assert seen[0].terminals[0].terminal_key == "negative"


def test_marking_reviewed_from_the_page_updates_the_row(qapp, controller, tmp_path) -> None:
    _record(controller, "A", _evidence_cycle(controller.data_directory / "inspections", "A"))
    page = _page(qapp, controller)

    _select_all_rows(page)
    page.mark_reviewed()
    qapp.processEvents()

    assert controller.list_failures()[0]["review_state"] == REVIEW_REVIEWED


def test_clearing_asks_first_and_says_what_is_at_stake(qapp, controller, tmp_path) -> None:
    """Never-exported and held records are named in the confirmation."""

    _record(controller, "A", _evidence_cycle(controller.data_directory / "inspections", "A"))
    controller.set_failures_kept(["A"], True)
    page = _page(qapp, controller)
    prompts: list[str] = []
    page.confirm_clear = lambda message: (prompts.append(message), False)[1]

    _select_all_rows(page)
    page.clear_selected()

    assert prompts and "held from retention" in prompts[0]
    assert "never been exported" in prompts[0]
    # Declined, so nothing was removed.
    assert len(controller.list_failures()) == 1


def test_declining_the_prompt_clears_nothing(qapp, controller, tmp_path) -> None:
    directory = _evidence_cycle(controller.data_directory / "inspections", "A")
    _record(controller, "A", directory)
    page = _page(qapp, controller)
    page.confirm_clear = lambda _message: False

    _select_all_rows(page)
    page.clear_selected()

    assert directory.is_dir()
    assert len(controller.list_failures()) == 1


def test_confirming_the_prompt_clears_the_selection(qapp, controller, tmp_path) -> None:
    directory = _evidence_cycle(controller.data_directory / "inspections", "A")
    _record(controller, "A", directory)
    page = _page(qapp, controller)
    page.confirm_clear = lambda _message: True

    _select_all_rows(page)
    page.clear_selected()
    qapp.processEvents()

    assert not directory.is_dir()
    assert controller.list_failures() == []


def test_clearing_with_nothing_selected_does_nothing(qapp, controller, tmp_path) -> None:
    """Clear never acts on the whole list implicitly."""

    directory = _evidence_cycle(controller.data_directory / "inspections", "A")
    _record(controller, "A", directory)
    page = _page(qapp, controller)
    page.confirm_clear = lambda _message: pytest.fail("should not have asked")

    page.table.clearSelection()
    page.clear_selected()

    assert directory.is_dir()


def test_the_label_dialog_preselects_nothing(qapp, controller, tmp_path) -> None:
    """The one interaction rule that protects the training set.

    A rejected part is where the classifier may have been wrong, so the dialog
    must not offer its answer as the default. An operator clicking straight
    through adds no samples at all rather than adding mislabelled ones.
    """

    record = _failure_with_frame(controller, tmp_path, detected="plus")
    page = _page(qapp, controller)

    dialog = page.build_label_dialog(record)

    assert dialog.labels() == {}
