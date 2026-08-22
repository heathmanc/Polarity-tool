from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from uuid import uuid4

from battery_inspector.build_info import INSPECTION_ENGINE
from battery_inspector.models import (
    LocatorSettings,
    Marking,
    MarkingClassifierSettings,
    NormalizedRect,
    Recipe,
    RecipeStatus,
    ReferenceCapture,
    TerminalRecipe,
    TerminalFinish,
    TerminalRole,
)


def full_to_parent(parent: NormalizedRect, child: NormalizedRect) -> NormalizedRect:
    """Convert a full-image rectangle into coordinates relative to ``parent``."""

    return NormalizedRect(
        x=(child.x - parent.x) / max(parent.width, 0.001),
        y=(child.y - parent.y) / max(parent.height, 0.001),
        width=child.width / max(parent.width, 0.001),
        height=child.height / max(parent.height, 0.001),
    ).clamped()


def parent_to_full(parent: NormalizedRect, child: NormalizedRect) -> NormalizedRect:
    """Convert a parent-relative rectangle into full-image coordinates."""

    return NormalizedRect(
        x=parent.x + child.x * parent.width,
        y=parent.y + child.y * parent.height,
        width=child.width * parent.width,
        height=child.height * parent.height,
    ).clamped()


@dataclass(slots=True)
class RecipeDraft:
    """Technician-facing state for a new immutable recipe revision."""

    recipe_id: str = field(default_factory=lambda: str(uuid4()))
    recipe_number: int = 0
    name: str = "NEW_BATTERY_MODEL"
    part_number: str = ""
    description: str = ""
    orientation_reference: str = "terminal_layout_and_case_outline"
    battery_roi: NormalizedRect = field(
        default_factory=lambda: NormalizedRect(0.24, 0.015, 0.59, 0.92)
    )
    terminal_rois: dict[str, NormalizedRect] = field(
        default_factory=lambda: {
            "negative": NormalizedRect(0.297, 0.093, 0.128, 0.192),
            "positive": NormalizedRect(0.644, 0.679, 0.128, 0.192),
        }
    )
    marking_rois: dict[str, NormalizedRect] = field(
        default_factory=lambda: {
            "negative": NormalizedRect(0.32, 0.31, 0.36, 0.36),
            "positive": NormalizedRect(0.32, 0.31, 0.36, 0.36),
        }
    )
    marking_roi_shapes: dict[str, str] = field(
        default_factory=lambda: {"negative": "circle", "positive": "circle"}
    )
    expected_markings: dict[str, Marking] = field(
        default_factory=lambda: {"negative": Marking.MINUS, "positive": Marking.PLUS}
    )
    expected_finishes: dict[str, TerminalFinish] = field(
        default_factory=lambda: {
            "negative": TerminalFinish.UNSPECIFIED,
            "positive": TerminalFinish.UNSPECIFIED,
        }
    )
    red_ring_required: dict[str, bool] = field(
        default_factory=lambda: {"negative": False, "positive": True}
    )
    validation_runs_required: int = 5
    validation_runs_passed: int = 0
    activate_on_finish: bool = False
    reference_image: ReferenceCapture | None = None
    reference_accepted: bool = False
    reference_changed: bool = False
    additional_terminals: list[TerminalRecipe] = field(default_factory=list)
    locator_settings: LocatorSettings = field(default_factory=LocatorSettings)
    classifier_settings: MarkingClassifierSettings = field(
        default_factory=MarkingClassifierSettings
    )
    validation_records: list[dict] = field(default_factory=list)
    validation_configuration_hash: str = ""

    @classmethod
    def from_recipe(cls, recipe: Recipe) -> "RecipeDraft":
        """Create an edit draft while leaving the stored revision untouched.

        A previous reference is loaded for review, but the technician must still
        explicitly select KEEP EXISTING or capture/accept a new frame.
        """

        draft = cls(
            recipe_id=recipe.recipe_id,
            recipe_number=recipe.recipe_number,
            name=recipe.name,
            part_number=recipe.part_number,
            description=recipe.description,
            orientation_reference=recipe.orientation_reference,
            battery_roi=replace(recipe.battery_roi),
            validation_runs_required=max(1, recipe.validation_runs_required),
            validation_runs_passed=0,
            activate_on_finish=False,
            reference_image=(recipe.reference_image.copied() if recipe.reference_image else None),
            reference_accepted=False,
            reference_changed=False,
            locator_settings=LocatorSettings.from_dict(recipe.locator_settings.to_dict()),
            classifier_settings=MarkingClassifierSettings.from_dict(
                recipe.classifier_settings.to_dict()
            ),
            validation_records=[],
            validation_configuration_hash="",
        )

        assigned_roles: set[TerminalRole] = set()
        for terminal in recipe.terminals:
            if terminal.role == TerminalRole.NEGATIVE and TerminalRole.NEGATIVE not in assigned_roles:
                key = "negative"
                assigned_roles.add(TerminalRole.NEGATIVE)
            elif terminal.role == TerminalRole.POSITIVE and TerminalRole.POSITIVE not in assigned_roles:
                key = "positive"
                assigned_roles.add(TerminalRole.POSITIVE)
            else:
                draft.additional_terminals.append(
                    replace(
                        terminal,
                        search_roi=replace(terminal.search_roi),
                        marking_roi=replace(terminal.marking_roi),
                    )
                )
                continue

            draft.terminal_rois[key] = parent_to_full(recipe.battery_roi, terminal.search_roi)
            draft.marking_rois[key] = replace(terminal.marking_roi)
            # Preserve the input geometry of the source revision. The wizard
            # upgrades these regions to taught circles only when the station's
            # bound ML model declares the circle crop contract. This lets an
            # existing legacy rectangle model be revalidated with the *same*
            # exact crop convention it was trained on instead of silently
            # changing the model input during an edit.
            draft.marking_roi_shapes[key] = terminal.marking_roi_shape
            draft.expected_markings[key] = terminal.expected_marking
            draft.expected_finishes[key] = terminal.expected_finish
            draft.red_ring_required[key] = terminal.red_ring_required

        return draft

    def set_reference(self, reference: ReferenceCapture, *, changed: bool) -> None:
        self.reference_image = reference.copied()
        self.reference_accepted = True
        self.reference_changed = changed
        self.reset_validation()

    def accept_existing_reference(self) -> None:
        if self.reference_image is None:
            raise ValueError("This recipe revision does not have an existing reference image.")
        self.reference_accepted = True
        self.reference_changed = False
        self.reset_validation()

    def clear_reference_acceptance(self) -> None:
        self.reference_accepted = False
        self.activate_on_finish = False

    def reset_validation(self) -> None:
        self.validation_runs_passed = 0
        self.validation_records.clear()
        self.validation_configuration_hash = ""
        self.activate_on_finish = False

    def configuration_fingerprint(self) -> str:
        reference_sha = self.reference_image.sha256 if self.reference_image else ""
        payload = {
            "inspection_engine": INSPECTION_ENGINE,
            "reference_sha256": reference_sha,
            "battery_roi": self.battery_roi.to_dict(),
            "orientation_reference": self.orientation_reference,
            "terminal_rois": {
                key: value.to_dict() for key, value in sorted(self.terminal_rois.items())
            },
            "marking_rois": {
                key: value.to_dict() for key, value in sorted(self.marking_rois.items())
            },
            "marking_roi_shapes": dict(sorted(self.marking_roi_shapes.items())),
            "expected_markings": {
                key: value.value for key, value in sorted(self.expected_markings.items())
            },
            "expected_finishes": {
                key: value.value for key, value in sorted(self.expected_finishes.items())
            },
            "red_ring_required": dict(sorted(self.red_ring_required.items())),
            "locator_settings": self.locator_settings.to_dict(),
            "classifier_settings": self.classifier_settings.to_dict(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    def ensure_validation_matches_configuration(self) -> str:
        fingerprint = self.configuration_fingerprint()
        if self.validation_configuration_hash not in {"", fingerprint}:
            self.reset_validation()
        if not self.validation_configuration_hash:
            self.validation_configuration_hash = fingerprint
        return fingerprint

    @property
    def validation_pass_record_count(self) -> int:
        fingerprint = self.ensure_validation_matches_configuration()
        return sum(
            1
            for record in self.validation_records
            if str(record.get("disposition", "")).strip().lower() == "pass"
            and str(record.get("configuration_hash", "")).strip() == fingerprint
            and str(record.get("inspection_engine", "")).strip()
            == INSPECTION_ENGINE
        )

    @property
    def validation_complete(self) -> bool:
        return self.validation_pass_record_count >= max(
            1, int(self.validation_runs_required)
        )

    def add_validation_record(self, record: dict) -> None:
        fingerprint = self.ensure_validation_matches_configuration()
        normalized = dict(record)
        normalized["configuration_hash"] = fingerprint
        normalized["inspection_engine"] = INSPECTION_ENGINE
        self.validation_records.append(normalized)
        self.validation_runs_passed = self.validation_pass_record_count

    def build_recipe(self, username: str, *, base_recipe: Recipe | None = None) -> Recipe:
        if self.reference_image is None or not self.reference_accepted:
            raise ValueError(
                "Capture and accept a recipe reference image, or explicitly keep the existing reference."
            )

        missing_finishes = [
            key
            for key in ("negative", "positive")
            if self.expected_finishes.get(key, TerminalFinish.UNSPECIFIED)
            == TerminalFinish.UNSPECIFIED
        ]
        if missing_finishes:
            raise ValueError(
                "Select SILVER or BRASS for each primary terminal before saving the recipe."
            )

        fingerprint = self.ensure_validation_matches_configuration()
        self.validation_runs_passed = sum(
            1
            for record in self.validation_records
            if str(record.get("disposition", "")).lower() == "pass"
            and str(record.get("configuration_hash", "")) == fingerprint
            and str(record.get("inspection_engine", "")) == INSPECTION_ENGINE
        )

        terminals = [
            TerminalRecipe(
                key="negative",
                name="Negative Terminal",
                role=TerminalRole.NEGATIVE,
                search_roi=full_to_parent(self.battery_roi, self.terminal_rois["negative"]),
                marking_roi=self.marking_rois["negative"].clamped(),
                expected_marking=self.expected_markings["negative"],
                red_ring_required=self.red_ring_required["negative"],
                expected_finish=self.expected_finishes["negative"],
                marking_roi_shape=self.marking_roi_shapes.get("negative", "circle"),
            ),
            TerminalRecipe(
                key="positive",
                name="Positive Terminal",
                role=TerminalRole.POSITIVE,
                search_roi=full_to_parent(self.battery_roi, self.terminal_rois["positive"]),
                marking_roi=self.marking_rois["positive"].clamped(),
                expected_marking=self.expected_markings["positive"],
                red_ring_required=self.red_ring_required["positive"],
                expected_finish=self.expected_finishes["positive"],
                marking_roi_shape=self.marking_roi_shapes.get("positive", "circle"),
            ),
            *[
                replace(
                    terminal,
                    search_roi=replace(terminal.search_roi),
                    marking_roi=replace(terminal.marking_roi),
                )
                for terminal in self.additional_terminals
            ],
        ]

        if base_recipe is None:
            recipe = Recipe.new(
                name=self.name.strip(),
                recipe_number=self.recipe_number,
                part_number=self.part_number.strip(),
                description=self.description.strip(),
                created_by=username,
                battery_roi=self.battery_roi,
                terminals=terminals,
                orientation_reference=self.orientation_reference,
                reference_image=self.reference_image,
            )
            recipe.recipe_id = self.recipe_id
        else:
            now = datetime.now(timezone.utc).isoformat()
            recipe = Recipe(
                recipe_id=base_recipe.recipe_id,
                recipe_number=self.recipe_number,
                name=self.name.strip(),
                part_number=self.part_number.strip(),
                description=self.description.strip(),
                revision=base_recipe.revision + 1,
                status=RecipeStatus.DRAFT,
                battery_roi=self.battery_roi.clamped(),
                orientation_reference=self.orientation_reference,
                terminals=terminals,
                created_by=base_recipe.created_by,
                created_at_utc=base_recipe.created_at_utc,
                updated_by=username,
                updated_at_utc=now,
                validation_runs_required=max(1, self.validation_runs_required),
                validation_runs_passed=int(self.validation_runs_passed),
                reference_image=self.reference_image.copied(),
                locator_settings=LocatorSettings.from_dict(
                    self.locator_settings.to_dict()
                ),
                classifier_settings=MarkingClassifierSettings.from_dict(
                    self.classifier_settings.to_dict()
                ),
                validation_records=[dict(item) for item in self.validation_records],
                validation_configuration_hash=self.validation_configuration_hash,
            )
            return recipe

        recipe.validation_runs_required = max(1, self.validation_runs_required)
        recipe.validation_runs_passed = int(self.validation_runs_passed)
        recipe.locator_settings = LocatorSettings.from_dict(
            self.locator_settings.to_dict()
        )
        recipe.classifier_settings = MarkingClassifierSettings.from_dict(
            self.classifier_settings.to_dict()
        )
        recipe.validation_records = [dict(item) for item in self.validation_records]
        recipe.validation_configuration_hash = self.validation_configuration_hash
        recipe.status = RecipeStatus.DRAFT
        return recipe
