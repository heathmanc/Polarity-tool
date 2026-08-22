from __future__ import annotations

from pathlib import Path

import pytest

from battery_inspector.data import RecipeRepository
from battery_inspector.models import Marking, RecipeStatus, TerminalFinish, TerminalRole
from battery_inspector.recipe_draft import RecipeDraft, parent_to_full


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PROJECT_ROOT / "battery_inspector" / "assets"


def _seeded_repository(tmp_path: Path) -> RecipeRepository:
    repository = RecipeRepository(tmp_path / "recipes.db")
    repository.seed_demo_data(ASSETS / "demo_battery.jpg")
    return repository


def _select_test_finishes(draft: RecipeDraft) -> None:
    draft.expected_finishes["negative"] = TerminalFinish.SILVER
    draft.expected_finishes["positive"] = TerminalFinish.BRASS


def test_edit_requires_explicit_reference_choice(tmp_path: Path) -> None:
    repository = _seeded_repository(tmp_path)
    original = repository.get_active_recipe()
    assert original is not None
    assert original.has_reference_image

    draft = RecipeDraft.from_recipe(original)
    assert draft.reference_image is not None
    assert draft.reference_accepted is False

    with pytest.raises(ValueError, match="explicitly keep"):
        draft.build_recipe("tech", base_recipe=original)


def test_edit_draft_round_trips_existing_recipe_geometry(tmp_path: Path) -> None:
    repository = _seeded_repository(tmp_path)
    original = repository.get_active_recipe()
    assert original is not None

    draft = RecipeDraft.from_recipe(original)
    draft.accept_existing_reference()
    _select_test_finishes(draft)
    negative = next(item for item in original.terminals if item.role == TerminalRole.NEGATIVE)
    positive = next(item for item in original.terminals if item.role == TerminalRole.POSITIVE)

    expected_negative_full = parent_to_full(original.battery_roi, negative.search_roi)
    expected_positive_full = parent_to_full(original.battery_roi, positive.search_roi)
    assert draft.terminal_rois["negative"].x == pytest.approx(expected_negative_full.x)
    assert draft.terminal_rois["negative"].y == pytest.approx(expected_negative_full.y)
    assert draft.terminal_rois["positive"].x == pytest.approx(expected_positive_full.x)
    assert draft.terminal_rois["positive"].y == pytest.approx(expected_positive_full.y)
    assert draft.expected_markings["negative"] == Marking.MINUS
    assert draft.expected_markings["positive"] == Marking.PLUS
    assert draft.validation_runs_passed == 0
    assert draft.activate_on_finish is False

    edited = draft.build_recipe("tech", base_recipe=original)
    assert edited.recipe_id == original.recipe_id
    assert edited.revision == original.revision + 1
    assert edited.status == RecipeStatus.DRAFT
    assert edited.created_at_utc == original.created_at_utc
    assert edited.validation_runs_passed == 0
    assert edited.reference_image is not None
    assert original.reference_image is not None
    assert edited.reference_image.path == original.reference_image.path
    assert edited.reference_image is not original.reference_image

    edited_negative = next(item for item in edited.terminals if item.role == TerminalRole.NEGATIVE)
    edited_positive = next(item for item in edited.terminals if item.role == TerminalRole.POSITIVE)
    assert edited_negative.search_roi.x == pytest.approx(negative.search_roi.x)
    assert edited_negative.search_roi.y == pytest.approx(negative.search_roi.y)
    assert edited_positive.search_roi.x == pytest.approx(positive.search_roi.x)
    assert edited_positive.search_roi.y == pytest.approx(positive.search_roi.y)

    # Building a draft cannot mutate the active revision.
    assert original.status == RecipeStatus.ACTIVE
    assert original.revision == 2


def test_saved_edit_creates_immutable_revision(tmp_path: Path) -> None:
    repository = _seeded_repository(tmp_path)
    original = repository.get_active_recipe()
    assert original is not None

    draft = RecipeDraft.from_recipe(original)
    draft.accept_existing_reference()
    _select_test_finishes(draft)
    draft.description = "Edited through the guided wizard"
    # A recipe edit always resets validation, even if stale UI state tries to set it.
    draft.validation_runs_passed = draft.validation_runs_required
    edited = draft.build_recipe("tech", base_recipe=original)
    assert edited.validation_runs_passed == 0
    repository.save_recipe(edited, username="tech", message="Edited recipe")

    revisions = repository.list_revisions(original.recipe_id)
    assert [item.revision for item in revisions[:2]] == [3, 2]
    assert revisions[0].description == "Edited through the guided wizard"
    assert revisions[0].status == RecipeStatus.DRAFT
    assert revisions[0].reference_image is not None
    assert revisions[1].status == RecipeStatus.ACTIVE
    assert revisions[1].description != revisions[0].description


def test_real_validation_records_are_bound_to_recipe_configuration(tmp_path: Path) -> None:
    repository = _seeded_repository(tmp_path)
    original = repository.get_active_recipe()
    assert original is not None

    draft = RecipeDraft.from_recipe(original)
    draft.accept_existing_reference()
    _select_test_finishes(draft)
    draft.add_validation_record(
        {
            "disposition": "pass",
            "cycle_id": "VALIDATE-1",
            "terminals": [],
        }
    )
    initial_hash = draft.validation_configuration_hash
    assert initial_hash
    assert draft.validation_runs_passed == 1

    validated = draft.build_recipe("tech", base_recipe=original)
    assert validated.validation_runs_passed == 1
    assert validated.validation_records[0]["configuration_hash"] == initial_hash

    # Any teach change invalidates all earlier validation evidence before save.
    draft.marking_rois["positive"].width *= 0.9
    changed = draft.build_recipe("tech", base_recipe=original)
    assert changed.validation_runs_passed == 0
    assert changed.validation_records == []
    assert changed.validation_configuration_hash != initial_hash


def test_recipe_edit_preserves_source_marking_crop_contract_until_model_binding(tmp_path: Path) -> None:
    original = _seeded_repository(tmp_path).get_active_recipe()
    assert original is not None
    source = {item.role: item.marking_roi_shape for item in original.terminals}
    draft = RecipeDraft.from_recipe(original)
    assert draft.marking_roi_shapes["negative"] == source[TerminalRole.NEGATIVE]
    assert draft.marking_roi_shapes["positive"] == source[TerminalRole.POSITIVE]
    draft.accept_existing_reference()
    _select_test_finishes(draft)
    edited = draft.build_recipe("tech", base_recipe=original)
    primary = {item.role: item for item in edited.terminals}
    assert primary[TerminalRole.NEGATIVE].marking_roi_shape == source[TerminalRole.NEGATIVE]
    assert primary[TerminalRole.POSITIVE].marking_roi_shape == source[TerminalRole.POSITIVE]


def test_new_or_edited_recipe_requires_both_terminal_finishes(tmp_path: Path) -> None:
    original = _seeded_repository(tmp_path).get_active_recipe()
    assert original is not None
    draft = RecipeDraft.from_recipe(original)
    draft.accept_existing_reference()

    with pytest.raises(ValueError, match="SILVER or BRASS"):
        draft.build_recipe("tech", base_recipe=original)

    _select_test_finishes(draft)
    edited = draft.build_recipe("tech", base_recipe=original)
    by_role = {terminal.role: terminal for terminal in edited.terminals}
    assert by_role[TerminalRole.NEGATIVE].expected_finish == TerminalFinish.SILVER
    assert by_role[TerminalRole.POSITIVE].expected_finish == TerminalFinish.BRASS
