from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from battery_inspector.build_info import INSPECTION_ENGINE
from battery_inspector.evidence import reference_capture_from_file
from battery_inspector.models import (
    LocatorSettings,
    Marking,
    MarkingClassifierSettings,
    NormalizedRect,
    Recipe,
    RecipeStatus,
    TerminalRecipe,
    TerminalRole,
)


class DuplicateRecipeIdentifier(ValueError):
    """A recipe number or name is already in use by a different recipe.

    Both are how the PLC names a product. If two recipes share one, the
    selector no longer identifies a single product and resolution silently
    picks whichever revision sorts highest -- so this is refused at the point
    it would be created, not discovered on the line.
    """


class RecipeRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _create_schema(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS recipes (
                    recipe_id TEXT NOT NULL,
                    recipe_number INTEGER NOT NULL DEFAULT 0,
                    name TEXT NOT NULL,
                    part_number TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (recipe_id, revision)
                );

                CREATE INDEX IF NOT EXISTS idx_recipes_name
                    ON recipes(name, revision DESC);

                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp_utc TEXT NOT NULL,
                    username TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inspections (
                    inspection_id TEXT PRIMARY KEY,
                    timestamp_utc TEXT NOT NULL,
                    recipe_id TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(recipes)").fetchall()
            }
            if "recipe_number" not in columns:
                connection.execute(
                    "ALTER TABLE recipes ADD COLUMN recipe_number INTEGER NOT NULL DEFAULT 0"
                )
            self._migrate_recipe_numbers(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_recipes_number "
                "ON recipes(recipe_number, revision DESC)"
            )

    @staticmethod
    def _migrate_recipe_numbers(connection: sqlite3.Connection) -> None:
        """Give every recipe family a stable positive production number.

        v0.18 and older payloads contain only a UUID. Keep any already-valid
        recipe number, then assign the next unused positive integer in original
        insertion order. Every revision of one recipe shares the same number.
        """

        families = connection.execute(
            """
            SELECT recipe_id, MIN(rowid) AS first_row
            FROM recipes
            GROUP BY recipe_id
            ORDER BY first_row
            """
        ).fetchall()
        used: set[int] = set()
        assignments: dict[str, int] = {}
        next_number = 1
        for family in families:
            recipe_id = str(family["recipe_id"])
            row = connection.execute(
                "SELECT payload_json, recipe_number FROM recipes "
                "WHERE recipe_id = ? ORDER BY revision DESC LIMIT 1",
                (recipe_id,),
            ).fetchone()
            candidate = 0
            if row is not None:
                try:
                    payload = json.loads(row["payload_json"])
                    candidate = int(
                        payload.get("recipe_number", row["recipe_number"] or 0) or 0
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    candidate = 0
            if candidate <= 0 or candidate in used:
                while next_number in used:
                    next_number += 1
                candidate = next_number
            used.add(candidate)
            next_number = max(next_number, candidate + 1)
            assignments[recipe_id] = candidate

        for recipe_id, number in assignments.items():
            rows = connection.execute(
                "SELECT revision, payload_json FROM recipes WHERE recipe_id = ?",
                (recipe_id,),
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                payload["recipe_number"] = number
                connection.execute(
                    "UPDATE recipes SET recipe_number = ?, payload_json = ? "
                    "WHERE recipe_id = ? AND revision = ?",
                    (
                        number,
                        json.dumps(payload, separators=(",", ":")),
                        recipe_id,
                        int(row["revision"]),
                    ),
                )

    def _assert_identifiers_are_free(self, recipe: Recipe) -> None:
        """Refuse a number or name that already belongs to another recipe.

        Revisions of the same recipe share both, which is the point: the PLC
        keeps naming one product as it is revised. Only a *different*
        ``recipe_id`` is a conflict.

        This checks rather than relying on a UNIQUE index, because an existing
        station may already carry a duplicate created before this rule. Those
        stay loadable and are reported by ``duplicate_identifiers()``; what is
        refused is making a new one.
        """

        name = recipe.name.strip()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT name FROM recipes WHERE recipe_number = ? AND recipe_id <> ? LIMIT 1",
                (int(recipe.recipe_number), recipe.recipe_id),
            ).fetchone()
            if row is not None:
                raise DuplicateRecipeIdentifier(
                    f"Recipe number {recipe.recipe_number} is already used by "
                    f"{row['name']}. The PLC selects a product by this number, "
                    "so it has to name exactly one recipe."
                )
            row = connection.execute(
                "SELECT recipe_number FROM recipes WHERE name = ? COLLATE NOCASE "
                "AND recipe_id <> ? LIMIT 1",
                (name, recipe.recipe_id),
            ).fetchone()
            if row is not None:
                raise DuplicateRecipeIdentifier(
                    f"Recipe name {name} is already used by recipe number "
                    f"{row['recipe_number']}. The PLC can select a product by "
                    "name, so it has to name exactly one recipe."
                )

    def duplicate_identifiers(self) -> list[str]:
        """Numbers and names that more than one recipe claims.

        Empty on a station that was built under the uniqueness rule. Non-empty
        means the PLC selector is ambiguous for those products and resolution
        will pick one arbitrarily; the duplicates must be resolved before the
        station runs them.
        """

        findings: list[str] = []
        with self._connection() as connection:
            for column, label in (
                ("recipe_number", "number"),
                ("name COLLATE NOCASE", "name"),
            ):
                rows = connection.execute(
                    f"SELECT {column} AS value, COUNT(DISTINCT recipe_id) AS claimants "  # noqa: S608
                    f"FROM recipes GROUP BY {column} HAVING claimants > 1"
                ).fetchall()
                findings.extend(
                    f"Recipe {label} {row['value']} is claimed by {row['claimants']} recipes"
                    for row in rows
                )
        return findings

    def save_recipe(self, recipe: Recipe, *, username: str, message: str = "Recipe saved") -> Recipe:
        if recipe.recipe_number <= 0:
            with self._connection() as connection:
                existing = connection.execute(
                    "SELECT recipe_number FROM recipes WHERE recipe_id = ? "
                    "ORDER BY revision DESC LIMIT 1",
                    (recipe.recipe_id,),
                ).fetchone()
                if existing is not None and int(existing["recipe_number"] or 0) > 0:
                    recipe.recipe_number = int(existing["recipe_number"])
                else:
                    row = connection.execute(
                        "SELECT COALESCE(MAX(recipe_number), 0) + 1 AS next_number FROM recipes"
                    ).fetchone()
                    recipe.recipe_number = max(
                        1, int(row["next_number"] if row else 1)
                    )
        self._assert_identifiers_are_free(recipe)
        now = datetime.now(timezone.utc).isoformat()
        recipe.updated_by = username
        recipe.updated_at_utc = now
        payload = json.dumps(recipe.to_dict(), separators=(",", ":"))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO recipes
                    (recipe_id, recipe_number, name, part_number, revision, status, payload_json, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recipe.recipe_id,
                    recipe.recipe_number,
                    recipe.name,
                    recipe.part_number,
                    recipe.revision,
                    recipe.status.value,
                    payload,
                    now,
                ),
            )
        self.add_audit_event(username=username, category="RECIPE", message=message, details=recipe.to_dict())
        return recipe

    def create_revision(self, recipe: Recipe, *, username: str) -> Recipe:
        now = datetime.now(timezone.utc).isoformat()
        revised = replace(
            recipe,
            revision=recipe.revision + 1,
            status=RecipeStatus.DRAFT,
            updated_by=username,
            updated_at_utc=now,
            validation_runs_passed=0,
            terminals=[replace(terminal) for terminal in recipe.terminals],
            reference_image=(recipe.reference_image.copied() if recipe.reference_image else None),
            locator_settings=LocatorSettings.from_dict(recipe.locator_settings.to_dict()),
            classifier_settings=MarkingClassifierSettings.from_dict(
                recipe.classifier_settings.to_dict()
            ),
            validation_records=[],
            validation_configuration_hash="",
        )
        return self.save_recipe(revised, username=username, message=f"Created revision {revised.revision}")

    def activate_recipe(self, recipe_id: str, revision: int, *, username: str) -> Recipe:
        recipe = self.get_recipe(recipe_id, revision)
        if recipe is None:
            raise KeyError(f"Recipe {recipe_id} revision {revision} was not found")

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM recipes WHERE status = ?",
                (RecipeStatus.ACTIVE.value,),
            ).fetchall()
            for row in rows:
                current = Recipe.from_dict(json.loads(row["payload_json"]))
                current.status = RecipeStatus.VALIDATED
                connection.execute(
                    "UPDATE recipes SET status = ?, payload_json = ? WHERE recipe_id = ? AND revision = ?",
                    (
                        current.status.value,
                        json.dumps(current.to_dict(), separators=(",", ":")),
                        current.recipe_id,
                        current.revision,
                    ),
                )

        recipe.status = RecipeStatus.ACTIVE
        self.save_recipe(recipe, username=username, message=f"Activated revision {revision}")
        return recipe

    def get_recipe(self, recipe_id: str, revision: int | None = None) -> Recipe | None:
        sql = "SELECT payload_json FROM recipes WHERE recipe_id = ?"
        params: tuple[object, ...]
        if revision is None:
            sql += " ORDER BY revision DESC LIMIT 1"
            params = (recipe_id,)
        else:
            sql += " AND revision = ? LIMIT 1"
            params = (recipe_id, revision)

        with self._connection() as connection:
            row = connection.execute(sql, params).fetchone()
        return Recipe.from_dict(json.loads(row["payload_json"])) if row else None

    def resolve_production_recipe(
        self,
        *,
        recipe_number: int | None = None,
        recipe_name: str = "",
    ) -> Recipe | None:
        """The revision that grades a part when the PLC names this recipe.

        Production eligibility is **validation**, not a separate activation
        step: the newest revision of the named recipe whose validation is
        complete is the one that runs. A revision that has not passed its
        required independent samples never grades a part, whatever its status
        says, so the gate that matters is unchanged.

        Returns None when the name or number is unknown, or when the recipe
        exists but no revision of it has completed validation. The caller must
        treat that as "this station cannot inspect this product" and refuse the
        trigger; it must never fall back to some other recipe.
        """

        if recipe_number is not None and int(recipe_number) > 0:
            column, value = "recipe_number", int(recipe_number)
        elif recipe_name.strip():
            # Case-insensitively, because uniqueness is enforced case-
            # insensitively: no two recipes may differ only by case, so folding
            # here cannot make the match ambiguous. It does stop a controller
            # sending "group31_xhd" from being refused a recipe stored as
            # "GROUP31_XHD".
            column, value = "name COLLATE NOCASE", recipe_name.strip()
        else:
            return None

        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM recipes WHERE {column} = ? "  # noqa: S608
                "ORDER BY revision DESC",
                (value,),
            ).fetchall()

        for row in rows:
            recipe = Recipe.from_dict(json.loads(row["payload_json"]))
            if recipe.status is RecipeStatus.RETIRED:
                continue
            if recipe.validation_complete:
                return recipe
        return None

    def production_recipe_count(self) -> int:
        """How many products this station could inspect if asked."""

        return sum(
            1
            for recipe in self.list_latest_recipes()
            if self.resolve_production_recipe(recipe_number=recipe.recipe_number) is not None
        )

    def get_active_recipe(self) -> Recipe | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM recipes WHERE status = ? ORDER BY updated_at_utc DESC LIMIT 1",
                (RecipeStatus.ACTIVE.value,),
            ).fetchone()
        return Recipe.from_dict(json.loads(row["payload_json"])) if row else None

    def list_latest_recipes(self) -> list[Recipe]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT r.payload_json
                FROM recipes r
                JOIN (
                    SELECT recipe_id, MAX(revision) AS max_revision
                    FROM recipes
                    GROUP BY recipe_id
                ) latest
                ON latest.recipe_id = r.recipe_id AND latest.max_revision = r.revision
                ORDER BY r.recipe_number, r.name COLLATE NOCASE
                """
            ).fetchall()
        return [Recipe.from_dict(json.loads(row["payload_json"])) for row in rows]

    def next_recipe_number(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(recipe_number), 0) + 1 AS next_number FROM recipes"
            ).fetchone()
        return max(1, int(row["next_number"] if row else 1))

    def list_revisions(self, recipe_id: str) -> list[Recipe]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM recipes WHERE recipe_id = ? ORDER BY revision DESC",
                (recipe_id,),
            ).fetchall()
        return [Recipe.from_dict(json.loads(row["payload_json"])) for row in rows]

    def delete_recipe(self, recipe_id: str, *, username: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM recipes WHERE recipe_id = ?", (recipe_id,))
        self.add_audit_event(
            username=username,
            category="RECIPE",
            message="Deleted recipe and all revisions",
            details={"recipe_id": recipe_id},
        )

    def add_audit_event(self, *, username: str, category: str, message: str, details: dict | None = None) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO audit_events
                    (event_id, timestamp_utc, username, category, message, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    datetime.now(timezone.utc).isoformat(),
                    username,
                    category,
                    message,
                    json.dumps(details or {}, separators=(",", ":")),
                ),
            )

    def list_audit_events(self, limit: int = 200) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT timestamp_utc, username, category, message, details_json
                FROM audit_events
                ORDER BY timestamp_utc DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_inspection(self, inspection_payload: dict) -> None:
        disposition = str(inspection_payload.get("disposition", "")).lower()
        trigger_source = str(inspection_payload.get("trigger_source", "")).upper()
        if disposition == "pass" and trigger_source != "RECIPE_VALIDATION":
            # Defense in depth: production PASS records are prohibited even if a
            # future caller bypasses AppController's fail-only persistence gate.
            return
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO inspections
                    (inspection_id, timestamp_utc, recipe_id, disposition, reason, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    inspection_payload["inspection_id"],
                    inspection_payload["timestamp_utc"],
                    inspection_payload["recipe_id"],
                    inspection_payload["disposition"],
                    inspection_payload["reason"],
                    json.dumps(inspection_payload, separators=(",", ":")),
                ),
            )

    def purge_passing_history(self) -> dict[str, int]:
        """Remove legacy production PASS rows and their per-cycle audit entries.

        v0.18 production counters are session-only and PASS cycles are never
        persisted. This bounded migration enforces that rule for databases
        carried forward from earlier releases without touching recipes,
        validation records, configuration, or non-PASS history.
        """

        with self._connection() as connection:
            passing_rows = connection.execute(
                "SELECT inspection_id, payload_json FROM inspections WHERE disposition = 'pass'"
            ).fetchall()
            production_ids: list[str] = []
            for row in passing_rows:
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if str(payload.get("trigger_source", "")).upper() != "RECIPE_VALIDATION":
                    production_ids.append(str(row["inspection_id"]))
            audit_rows = connection.execute(
                """
                SELECT COUNT(*) FROM audit_events
                WHERE category = 'INSPECTION' AND message LIKE 'PASS:%'
                """
            ).fetchone()
            if production_ids:
                connection.executemany(
                    "DELETE FROM inspections WHERE inspection_id = ?",
                    [(identifier,) for identifier in production_ids],
                )
            connection.execute(
                """
                DELETE FROM audit_events
                WHERE category = 'INSPECTION' AND message LIKE 'PASS:%'
                """
            )
        return {
            "inspection_rows": len(production_ids),
            "audit_rows": int(audit_rows[0] if audit_rows else 0),
        }

    def inspection_summary(self, recent_limit: int = 13) -> dict[str, object]:
        """Return a compatibility summary of retained product-result rows.

        v0.18 runtime counters no longer call this method because they are
        session-only. It remains for older integrations and deliberately ignores
        validation captures and rows without cycle-owned evidence.
        """

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT timestamp_utc, disposition, payload_json
                FROM inspections
                WHERE disposition IN ('pass', 'reject')
                ORDER BY timestamp_utc ASC
                """
            ).fetchall()

        valid: list[tuple[str, bool]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if int(payload.get("record_schema_version", 0) or 0) < 2:
                continue
            if payload.get("analysis_ready") is not True:
                continue
            if str(payload.get("trigger_source", "")).upper() == "RECIPE_VALIDATION":
                continue
            if not str(payload.get("frame_id", "")).strip():
                continue
            if not str(payload.get("full_image_path", "")).strip():
                continue
            valid.append(
                (
                    str(row["timestamp_utc"]),
                    str(row["disposition"]) == "pass",
                )
            )

        passed = sum(1 for _timestamp, item_passed in valid if item_passed)
        rejected = len(valid) - passed
        recent_count = max(1, int(recent_limit))
        return {
            "part_count": len(valid),
            "pass_count": passed,
            "fail_count": rejected,
            "recent": [item_passed for _timestamp, item_passed in valid[-recent_count:]],
        }

    @staticmethod
    def _mark_bundled_demo_validated(recipe: Recipe) -> None:
        """Attach explicit fixture-only validation records to bundled examples.

        Numeric pass counts from older releases are not sufficient for user
        recipes. The bundled simulation is the sole exception: it ships with a
        controlled known-good reference and reversed regression image, so its
        prevalidated state is represented by explicit configuration-bound fixture
        records rather than by a bare integer.
        """

        required = max(1, int(recipe.validation_runs_required))
        fingerprint = "BUNDLED_DEMO_VALIDATION_V1"
        recipe.validation_configuration_hash = fingerprint
        recipe.validation_records = [
            {
                "disposition": "pass",
                "configuration_hash": fingerprint,
                "source": "BUNDLED_DEMO_FIXTURE",
                "inspection_engine": INSPECTION_ENGINE,
                "fixture_index": index + 1,
            }
            for index in range(required)
        ]
        recipe.validation_runs_passed = required

    def seed_demo_data(self, reference_image_path: Path | None = None) -> None:
        reference_image_path = self._preferred_demo_reference(reference_image_path)
        if self.list_latest_recipes():
            self._migrate_bundled_demo_reference(reference_image_path)
            return

        reference_image = None
        if reference_image_path is not None and reference_image_path.is_file():
            reference_image = reference_capture_from_file(
                reference_image_path,
                source="BUNDLED_DEMO_REFERENCE",
                camera_backend="bundled-asset",
                camera_description="Bundled demonstration image",
            )

        battery_roi = NormalizedRect(0.24, 0.015, 0.59, 0.92)
        negative = TerminalRecipe(
            key="negative",
            name="Negative Terminal (Upper Left)",
            role=TerminalRole.NEGATIVE,
            search_roi=NormalizedRect(0.095, 0.085, 0.217, 0.209),
            marking_roi=NormalizedRect(0.32, 0.31, 0.36, 0.36),
            expected_marking=Marking.MINUS,
            red_ring_required=False,
        )
        positive = TerminalRecipe(
            key="positive",
            name="Positive Terminal (Lower Right)",
            role=TerminalRole.POSITIVE,
            search_roi=NormalizedRect(0.685, 0.721, 0.217, 0.209),
            marking_roi=NormalizedRect(0.32, 0.31, 0.36, 0.36),
            expected_marking=Marking.PLUS,
            red_ring_required=True,
        )
        active = Recipe.new(
            name="GROUP31_XHD",
            part_number="12345678",
            description="Group 31 top-post demonstration battery",
            created_by="system",
            battery_roi=battery_roi,
            terminals=[negative, positive],
            reference_image=reference_image,
        )
        active.status = RecipeStatus.ACTIVE
        active.revision = 2
        self._mark_bundled_demo_validated(active)
        self.save_recipe(active, username="system", message="Seeded active demonstration recipe")

        examples = [
            ("GROUP24_STD", "87654321", "Group 24 standard", Marking.MINUS, False),
            ("GROUP27_AGM", "22334455", "Group 27 AGM", Marking.BLANK, False),
            ("GROUP8D_HEAVY", "99887766", "8D heavy duty", Marking.MINUS, True),
            ("GROUP48_LITHIUM", "55667788", "48 V lithium module", Marking.BLANK, False),
        ]
        for index, (name, part, description, negative_mark, positive_ring) in enumerate(examples, start=1):
            recipe = Recipe.new(
                name=name,
                part_number=part,
                description=description,
                created_by="system",
                battery_roi=NormalizedRect(0.18, 0.08, 0.64, 0.82),
                terminals=[
                    replace(negative, expected_marking=negative_mark),
                    replace(positive, red_ring_required=positive_ring),
                ],
                reference_image=reference_image,
            )
            recipe.revision = index
            # Additional layouts are UI/recipe-wizard examples only. They do not
            # have battery-family-specific reference evidence and must never look
            # production-qualified merely because the database was seeded.
            recipe.status = RecipeStatus.DRAFT
            recipe.validation_runs_passed = 0
            recipe.validation_records = []
            recipe.validation_configuration_hash = ""
            self.save_recipe(recipe, username="system", message="Seeded draft demonstration recipe")

    @staticmethod
    def _preferred_demo_reference(reference_image_path: Path | None) -> Path | None:
        """Select the known-good bundled reference when an older caller passes
        the intentionally reversed demonstration inspection image.

        Releases through v0.6 seeded ``demo_battery.jpg`` as both the live mock
        image and recipe reference.  A template classifier would correctly match
        that image to itself and therefore learn the defect as the expected
        answer.  Keeping this compatibility shim makes old tests/installations
        migrate safely without requiring a database reset.
        """

        if reference_image_path is None:
            bundled = (
                Path(__file__).resolve().parents[1]
                / "assets"
                / "demo_reference_good.png"
            )
            return bundled if bundled.is_file() else None
        candidate = Path(reference_image_path)
        if candidate.name.lower() == "demo_battery.jpg":
            known_good = candidate.with_name("demo_reference_good.png")
            if known_good.is_file():
                return known_good
        return candidate

    def _migrate_bundled_demo_reference(
        self,
        reference_image_path: Path | None,
    ) -> None:
        """Repair only system-supplied demo references from older releases.

        User-captured references are never modified. The controlled
        ``GROUP31_XHD`` fixture remains available for HMI regression testing.
        Other system-seeded layouts are explicitly demoted to DRAFT because they
        do not include battery-family-specific validation evidence.
        """

        if reference_image_path is None or not reference_image_path.is_file():
            return
        replacement = reference_capture_from_file(
            reference_image_path,
            source="BUNDLED_DEMO_REFERENCE",
            camera_backend="bundled-asset",
            camera_description="Bundled known-good demonstration reference",
        )
        recipes: list[Recipe] = []
        for latest in self.list_latest_recipes():
            recipes.extend(self.list_revisions(latest.recipe_id))

        primary_demo: Recipe | None = None
        demoted_active_demo = False
        for recipe in recipes:
            reference = recipe.reference_image
            if reference is None:
                continue
            if reference.source.strip().upper() != "BUNDLED_DEMO_REFERENCE":
                continue
            old_name = Path(reference.path).name.lower()
            if old_name not in {"demo_battery.jpg", "demo_reference_good.png"}:
                continue

            is_primary_demo = recipe.name == "GROUP31_XHD"
            if is_primary_demo and (
                primary_demo is None or recipe.revision > primary_demo.revision
            ):
                primary_demo = recipe

            changed = False
            if not (
                old_name == reference_image_path.name.lower()
                and reference.sha256 == replacement.sha256
            ):
                recipe.reference_image = replacement.copied()
                changed = True

            if is_primary_demo:
                if recipe.status in {RecipeStatus.ACTIVE, RecipeStatus.VALIDATED}:
                    if not recipe.validation_complete:
                        self._mark_bundled_demo_validated(recipe)
                        changed = True
            else:
                if recipe.status == RecipeStatus.ACTIVE:
                    demoted_active_demo = True
                if recipe.status != RecipeStatus.DRAFT:
                    recipe.status = RecipeStatus.DRAFT
                    changed = True
                if (
                    recipe.validation_runs_passed
                    or recipe.validation_records
                    or recipe.validation_configuration_hash
                ):
                    recipe.validation_runs_passed = 0
                    recipe.validation_records = []
                    recipe.validation_configuration_hash = ""
                    changed = True

            if changed:
                self.save_recipe(
                    recipe,
                    username="system",
                    message=(
                        "Migrated bundled primary recipe reference"
                        if is_primary_demo
                        else "Demoted unqualified bundled example recipe to draft"
                    ),
                )

        # If an older release allowed one of the unqualified example layouts to
        # become ACTIVE, restore the controlled primary demonstration fixture so
        # the application has a safe, deterministic commissioning recipe.
        if demoted_active_demo and primary_demo is not None:
            primary_demo.status = RecipeStatus.ACTIVE
            primary_demo.reference_image = replacement.copied()
            self._mark_bundled_demo_validated(primary_demo)
            self.save_recipe(
                primary_demo,
                username="system",
                message="Restored controlled primary demonstration recipe",
            )
