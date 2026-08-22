import json
import shutil
from pathlib import Path

from battery_inspector.data import RecipeRepository
from battery_inspector.models import RecipeStatus


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PROJECT_ROOT / "battery_inspector" / "assets"


def test_repository_seeds_and_activates(tmp_path: Path) -> None:
    repository = RecipeRepository(tmp_path / "test.db")
    repository.seed_demo_data(ASSETS / "demo_battery.jpg")
    recipes = repository.list_latest_recipes()
    assert len(recipes) == 5
    assert [recipe.recipe_number for recipe in recipes] == [1, 2, 3, 4, 5]
    assert repository.next_recipe_number() == 6
    active = repository.get_active_recipe()
    assert active is not None
    assert active.name == "GROUP31_XHD"
    assert active.status == RecipeStatus.ACTIVE
    assert active.has_reference_image is True
    assert active.reference_image is not None
    assert Path(active.reference_image.path).name == "demo_reference_good.png"

    target = next(recipe for recipe in recipes if recipe.name == "GROUP24_STD")
    assert target.status == RecipeStatus.DRAFT
    assert target.validation_complete is False
    assert target.validation_runs_passed == 0
    activated = repository.activate_recipe(target.recipe_id, target.revision, username="tester")
    assert activated.status == RecipeStatus.ACTIVE
    current = repository.get_active_recipe()
    assert current is not None
    assert current.recipe_id == target.recipe_id


def test_demo_seed_without_explicit_asset_uses_bundled_known_good_reference(
    tmp_path: Path,
) -> None:
    repository = RecipeRepository(tmp_path / "test.db")

    repository.seed_demo_data()

    active = repository.get_active_recipe()
    assert active is not None
    assert active.reference_image is not None
    assert Path(active.reference_image.path).name == "demo_reference_good.png"
    assert active.reference_image.source == "BUNDLED_DEMO_REFERENCE"


def test_repository_refuses_production_pass_records_and_summary_counts_failures(
    tmp_path: Path,
) -> None:
    repository = RecipeRepository(tmp_path / "test.db")

    def save(
        identifier: str,
        disposition: str,
        *,
        evidence_backed: bool = True,
    ) -> None:
        payload = {
            "inspection_id": identifier,
            "timestamp_utc": f"2026-08-19T12:00:0{identifier[-1]}+00:00",
            "recipe_id": "recipe-1",
            "disposition": disposition,
            "reason": disposition.upper(),
        }
        if evidence_backed:
            payload.update(
                {
                    "record_schema_version": 2,
                    "analysis_ready": True,
                    "frame_id": f"frame-{identifier}",
                    "full_image_path": f"/evidence/{identifier}/full.png",
                }
            )
        repository.save_inspection(payload)

    save("inspection-1", "pass")
    save("inspection-2", "reject")
    save("inspection-3", "not_ready")
    save("inspection-4", "system_fault")
    # Production PASS is refused regardless of whether an old caller supplies
    # evidence metadata.
    save("inspection-5", "pass", evidence_backed=False)

    summary = repository.inspection_summary()
    assert summary["part_count"] == 1
    assert summary["pass_count"] == 0
    assert summary["fail_count"] == 1
    assert summary["recent"] == [False]


def test_purge_passing_history_keeps_validation_and_non_pass_records(
    tmp_path: Path,
) -> None:
    repository = RecipeRepository(tmp_path / "test.db")

    def save(identifier: str, disposition: str, trigger_source: str) -> None:
        repository.save_inspection(
            {
                "inspection_id": identifier,
                "timestamp_utc": "2026-08-20T12:00:00+00:00",
                "recipe_id": "recipe-1",
                "disposition": disposition,
                "reason": disposition.upper(),
                "trigger_source": trigger_source,
            }
        )

    legacy_pass = {
        "inspection_id": "production-pass",
        "timestamp_utc": "2026-08-20T12:00:00+00:00",
        "recipe_id": "recipe-1",
        "disposition": "pass",
        "reason": "PASS",
        "trigger_source": "PLC",
    }
    with repository._connection() as connection:  # type: ignore[attr-defined]
        connection.execute(
            """
            INSERT INTO inspections
                (inspection_id, timestamp_utc, recipe_id, disposition, reason, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_pass["inspection_id"],
                legacy_pass["timestamp_utc"],
                legacy_pass["recipe_id"],
                legacy_pass["disposition"],
                legacy_pass["reason"],
                json.dumps(legacy_pass),
            ),
        )
    save("validation-pass", "pass", "RECIPE_VALIDATION")
    save("production-fail", "reject", "PLC")

    report = repository.purge_passing_history()

    assert report["inspection_rows"] == 1
    with repository._connection() as connection:  # type: ignore[attr-defined]
        identifiers = {
            str(row[0])
            for row in connection.execute("SELECT inspection_id FROM inspections").fetchall()
        }
    assert identifiers == {"validation-pass", "production-fail"}


def test_repository_migrates_only_the_legacy_bundled_demo_reference(
    tmp_path: Path,
) -> None:
    asset_directory = tmp_path / "assets"
    asset_directory.mkdir()
    legacy_path = asset_directory / "demo_battery.jpg"
    shutil.copy2(ASSETS / "demo_battery.jpg", legacy_path)
    repository = RecipeRepository(tmp_path / "test.db")
    repository.seed_demo_data(legacy_path)
    before = repository.get_active_recipe()
    assert before is not None and before.reference_image is not None
    assert Path(before.reference_image.path).name == "demo_battery.jpg"
    legacy_sha = before.reference_image.sha256

    known_good = asset_directory / "demo_reference_good.png"
    shutil.copy2(ASSETS / "demo_reference_good.png", known_good)
    repository.seed_demo_data(legacy_path)

    after = repository.get_active_recipe()
    assert after is not None and after.reference_image is not None
    assert Path(after.reference_image.path).name == "demo_reference_good.png"
    assert after.reference_image.sha256 != legacy_sha
    assert after.status == RecipeStatus.ACTIVE


def test_repository_repairs_bundled_demo_validation_records_without_replacing_user_data(
    tmp_path: Path,
) -> None:
    repository = RecipeRepository(tmp_path / "test.db")
    known_good = ASSETS / "demo_reference_good.png"
    repository.seed_demo_data(known_good)
    active = repository.get_active_recipe()
    assert active is not None
    assert active.reference_image is not None

    active.validation_records = []
    active.validation_configuration_hash = ""
    active.validation_runs_passed = active.validation_runs_required
    repository.save_recipe(
        active,
        username="test",
        message="Simulate legacy numeric-only demo validation",
    )
    assert repository.get_active_recipe().validation_complete is False

    repository.seed_demo_data(known_good)

    repaired = repository.get_active_recipe()
    assert repaired is not None
    assert repaired.validation_complete is True
    assert repaired.validation_pass_record_count == repaired.validation_runs_required
    assert all(
        record.get("source") == "BUNDLED_DEMO_FIXTURE"
        for record in repaired.validation_records
    )


def test_demo_migration_demotes_unqualified_example_and_restores_primary(
    tmp_path: Path,
) -> None:
    repository = RecipeRepository(tmp_path / "test.db")
    repository.seed_demo_data(ASSETS / "demo_reference_good.png")
    example = next(
        item for item in repository.list_latest_recipes() if item.name == "GROUP24_STD"
    )

    repository.activate_recipe(example.recipe_id, example.revision, username="legacy")
    assert repository.get_active_recipe() is not None
    assert repository.get_active_recipe().name == "GROUP24_STD"

    repository.seed_demo_data(ASSETS / "demo_reference_good.png")

    active = repository.get_active_recipe()
    assert active is not None
    assert active.name == "GROUP31_XHD"
    migrated_example = repository.get_recipe(example.recipe_id, example.revision)
    assert migrated_example is not None
    assert migrated_example.status == RecipeStatus.DRAFT
    assert migrated_example.validation_complete is False
    assert migrated_example.validation_records == []


def test_repository_loads_v081_classifier_settings_payload(tmp_path: Path) -> None:
    """Existing v0.8.1 recipe JSON must not prevent v0.9.x startup."""

    repository = RecipeRepository(tmp_path / "test.db")
    repository.seed_demo_data(ASSETS / "demo_reference_good.png")
    active = repository.get_active_recipe()
    assert active is not None

    payload = active.to_dict()
    classifier = payload["classifier_settings"]
    classifier["terminal_top_conditional_minimum_geometry_confidence"] = (
        classifier.pop("terminal_top_conditional_geometry_confidence")
    )

    with repository._connection() as connection:
        connection.execute(
            """
            UPDATE recipes
            SET payload_json = ?
            WHERE recipe_id = ? AND revision = ?
            """,
            (
                json.dumps(payload, separators=(",", ":")),
                active.recipe_id,
                active.revision,
            ),
        )

    loaded = repository.list_latest_recipes()
    migrated = next(item for item in loaded if item.recipe_id == active.recipe_id)
    assert (
        migrated.classifier_settings.terminal_top_conditional_geometry_confidence
        == active.classifier_settings.terminal_top_conditional_geometry_confidence
    )
