from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.controller import AppController
from battery_inspector.models import Recipe, RecipeStatus
from battery_inspector.ui.widgets import (
    AMBER,
    BLUE,
    LabeledValue,
    PageNavigator,
    PanelFrame,
)
from battery_inspector.ui.wizard import RecipeWizardDialog


class RecipesPage(QWidget):
    recipe_activated = Signal(object)

    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._recipes: list[Recipe] = []
        self._visible_recipes: list[Recipe] = []
        self._page_index = 0
        self._page_size = 10

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("RECIPES")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(10)
        root.addLayout(content, 1)

        list_panel = PanelFrame()
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(12, 12, 12, 12)
        toolbar = QHBoxLayout()
        self.new_button = QPushButton("＋  NEW RECIPE")
        self.new_button.setObjectName("PrimaryButton")
        self.edit_button = QPushButton("EDIT / NEW REVISION")
        self.import_button = QPushButton("IMPORT")
        self.export_button = QPushButton("EXPORT  ▾")
        # Two exports with very different meanings, so the technician picks one
        # by name rather than discovering the difference after moving a file to
        # another machine.
        self.export_menu = QMenu(self)
        self.export_geometry_action = QAction("Geometry template (JSON)…", self)
        self.export_package_action = QAction("Full recipe package (ZIP)…", self)
        self.export_menu.addAction(self.export_package_action)
        self.export_menu.addAction(self.export_geometry_action)
        self.export_button.setMenu(self.export_menu)
        self.delete_button = QPushButton("DELETE")
        self.delete_button.setObjectName("DangerButton")
        toolbar.addWidget(self.new_button)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.import_button)
        toolbar.addWidget(self.export_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addStretch(1)
        list_layout.addLayout(toolbar)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["NO.", "RECIPE NAME", "PART NUMBER", "DESCRIPTION", "REV.", "STATUS"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        list_layout.addWidget(self.table, 1)
        self.pager = PageNavigator("RECIPE PAGE")
        self.pager.previous_requested.connect(lambda: self._set_page(self._page_index - 1))
        self.pager.next_requested.connect(lambda: self._set_page(self._page_index + 1))
        list_layout.addWidget(self.pager)
        self.total_label = QLabel("SHOWING 0 OF 0 RECIPES")
        self.total_label.setProperty("muted", True)
        list_layout.addWidget(self.total_label)
        content.addWidget(list_panel, 4)

        detail = PanelFrame()
        detail.setMinimumWidth(340)
        detail.setMaximumWidth(460)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        detail_title = QLabel("SELECTED RECIPE")
        detail_title.setObjectName("PanelTitle")
        detail_layout.addWidget(detail_title)
        self.number = LabeledValue("Recipe number")
        self.name = LabeledValue("Recipe name")
        self.part = LabeledValue("Part number")
        self.description = LabeledValue("Description")
        self.revision = LabeledValue("Revision")
        self.status = LabeledValue("Status")
        self.reference = LabeledValue("Reference image")
        self.validation = LabeledValue("Validation")
        self.classifier = LabeledValue("Classifier")
        self.model_binding = LabeledValue("ML model binding")
        self.readiness = LabeledValue("Inspection readiness")
        for item in (
            self.number,
            self.name,
            self.part,
            self.description,
            self.revision,
            self.status,
            self.reference,
            self.validation,
            self.classifier,
            self.model_binding,
            self.readiness,
        ):
            detail_layout.addWidget(item)
        detail_layout.addStretch(1)
        self.activate_button = QPushButton("USE FOR MANUAL TRIGGERS")
        self.activate_button.setObjectName("PrimaryButton")
        self.revisions_button = QPushButton("MANAGE REVISIONS")
        detail_layout.addWidget(self.activate_button)
        detail_layout.addWidget(self.revisions_button)
        content.addWidget(detail, 1)

        self.new_button.clicked.connect(self.open_new_recipe_wizard)
        self.edit_button.clicked.connect(self.open_edit_recipe_wizard)
        self.table.itemDoubleClicked.connect(
            lambda _item: self.open_edit_recipe_wizard()
        )
        self.import_button.clicked.connect(self.import_recipe)
        self.export_geometry_action.triggered.connect(self.export_recipe)
        self.export_package_action.triggered.connect(self.export_recipe_package)
        self.delete_button.clicked.connect(self.delete_recipe)
        self.activate_button.clicked.connect(self.activate_selected)
        self.revisions_button.clicked.connect(self.show_revisions)

    def selected_recipe(self) -> Recipe | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._visible_recipes):
            return None
        return self._visible_recipes[row]

    def set_recipes(self, recipes: list[Recipe]) -> None:
        current = self.selected_recipe()
        selected_key = (current.recipe_id, current.revision) if current else None
        self._recipes = recipes
        if selected_key is not None:
            for index, recipe in enumerate(recipes):
                if selected_key == (recipe.recipe_id, recipe.revision):
                    self._page_index = index // self._page_size
                    break
        self._render_page(selected_key)

    def _set_page(self, page_index: int) -> None:
        self._page_index = int(page_index)
        self._render_page(None)

    def _render_page(self, selected_key: tuple[str, int] | None) -> None:
        page_count = max(1, math.ceil(len(self._recipes) / self._page_size))
        self._page_index = max(0, min(self._page_index, page_count - 1))
        start = self._page_index * self._page_size
        end = min(len(self._recipes), start + self._page_size)
        self._visible_recipes = self._recipes[start:end]

        self.table.clearContents()
        self.table.setRowCount(len(self._visible_recipes))
        selected_row = -1
        for row, recipe in enumerate(self._visible_recipes):
            values = [
                str(recipe.recipe_number),
                recipe.name,
                recipe.part_number,
                recipe.description,
                str(recipe.revision),
                recipe.status.value.upper(),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, recipe.recipe_id)
                if column == 5:
                    if recipe.status == RecipeStatus.ACTIVE:
                        item.setForeground(QBrush(QColor(BLUE)))
                    elif recipe.status == RecipeStatus.DRAFT:
                        item.setForeground(QBrush(QColor(AMBER)))
                self.table.setItem(row, column, item)
            if selected_key == (recipe.recipe_id, recipe.revision):
                selected_row = row

        self.pager.set_page(
            self._page_index,
            page_count,
            f"{len(self._recipes)} RECIPES",
        )
        if self._visible_recipes:
            self.total_label.setText(
                f"SHOWING {start + 1}–{end} OF {len(self._recipes)} RECIPES"
            )
            self.table.selectRow(selected_row if selected_row >= 0 else 0)
        else:
            self.total_label.setText("SHOWING 0 OF 0 RECIPES")
        self._selection_changed()

    def _detail_fields(self) -> tuple[LabeledValue, ...]:
        return (
            self.number,
            self.name,
            self.part,
            self.description,
            self.revision,
            self.status,
            self.reference,
            self.validation,
            self.classifier,
            self.model_binding,
            self.readiness,
        )

    def _selection_changed(self) -> None:
        recipe = self.selected_recipe()
        if recipe is None:
            for item in self._detail_fields():
                item.set_value("—")
            for button in (
                self.activate_button,
                self.edit_button,
                self.export_button,
                self.delete_button,
                self.revisions_button,
            ):
                button.setDisabled(True)
            return

        self.number.set_value(str(recipe.recipe_number))
        self.name.set_value(recipe.name)
        self.part.set_value(recipe.part_number)
        self.description.set_value(recipe.description or "—")
        self.revision.set_value(str(recipe.revision))
        status_tone = (
            "info"
            if recipe.status == RecipeStatus.ACTIVE
            else "warning" if recipe.status == RecipeStatus.DRAFT else None
        )
        self.status.set_value(recipe.status.value.upper(), status_tone)
        if recipe.reference_image is not None and Path(recipe.reference_image.path).is_file():
            self.reference.set_value(
                f"CAPTURED — {recipe.reference_image.width_px} × "
                f"{recipe.reference_image.height_px}"
            )
        else:
            self.reference.set_value("MISSING — EDIT RECIPE TO CAPTURE", "bad")
        self.validation.set_value(
            f"{recipe.validation_runs_passed} / {recipe.validation_runs_required}",
            "good"
            if recipe.validation_runs_passed >= recipe.validation_runs_required
            else "warning",
        )
        classifier_settings = recipe.classifier_settings.normalized()
        self.classifier.set_value(classifier_settings.method.upper())
        if classifier_settings.method == "onnx_ml":
            self.model_binding.set_value(
                f"{classifier_settings.ml_model_id or 'UNBOUND'} "
                f"{classifier_settings.ml_model_version or ''}\n"
                f"SHA {classifier_settings.ml_model_sha256[:12] or 'MISSING'}"
            )
        else:
            station = self.controller.ml_model_info(require_runtime=False)
            if station.get("model_sha256") and self.controller.config.ml.use_for_new_revisions:
                self.model_binding.set_value(
                    "LEGACY REVISION — edit/new revision to bind current ML candidate",
                    "warning",
                )
            else:
                self.model_binding.set_value("NOT ML-BOUND")

        readiness = self.controller.inspection_readiness(recipe)
        issues = list(readiness.get("issues", []))
        if readiness.get("ready"):
            self.readiness.set_value("READY")
        else:
            self.readiness.set_value(
                "NOT READY\n" + "\n".join(str(item) for item in issues),
                "warning",
            )

        activation_allowed = bool(
            recipe.status != RecipeStatus.ACTIVE
            and recipe.validation_runs_passed >= recipe.validation_runs_required
            and readiness.get("ready")
        )
        self.activate_button.setEnabled(activation_allowed)
        if recipe.status == RecipeStatus.ACTIVE:
            self.activate_button.setText("IN USE FOR MANUAL TRIGGERS")
        elif activation_allowed:
            self.activate_button.setText("USE FOR MANUAL TRIGGERS")
        else:
            self.activate_button.setText("BLOCKED — NOT READY")
        station_model = self.controller.ml_model_info(require_runtime=False)
        current_hash = str(station_model.get("model_sha256", ""))
        needs_ml_revision = bool(
            current_hash
            and self.controller.config.ml.use_for_new_revisions
            and recipe.classifier_settings.normalized().ml_model_sha256 != current_hash
        )
        self.edit_button.setText(
            "CREATE ML REVISION" if needs_ml_revision else "EDIT / NEW REVISION"
        )
        self.edit_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self.delete_button.setEnabled(True)
        self.revisions_button.setEnabled(True)

    def _open_wizard(
        self,
        *,
        recipe: Recipe | None = None,
        initial_reference_action: str = "choose",
        template_mode: bool = False,
    ) -> None:
        wizard = RecipeWizardDialog(
            controller=self.controller,
            username=self.controller.config.operator_name,
            recipe=recipe,
            initial_reference_action=initial_reference_action,
            template_mode=template_mode,
            parent=self,
        )
        wizard.recipe_ready.connect(self._recipe_saved_from_wizard)
        wizard.exec()

    def open_new_recipe_wizard(self) -> None:
        self._open_wizard(initial_reference_action="choose")

    def open_edit_recipe_wizard(self) -> None:
        """Open the wizard on its reference step, which asks the question itself.

        This used to put a dialog in front of the wizard asking whether to
        capture a new reference or keep the existing one -- and then step 1
        asked exactly the same thing, with the same two buttons. Worse, the
        dialog asked blind: the technician could not see the existing reference,
        the current scene, or the quality gate, which are the things the answer
        depends on. Step 1 shows all three, and still refuses to continue until
        one of them is chosen explicitly, so nothing about the reference policy
        is relaxed by dropping the dialog.
        """

        recipe = self.selected_recipe()
        if recipe is None:
            return
        self._open_wizard(recipe=recipe, initial_reference_action="choose")

    def _recipe_saved_from_wizard(self, recipe: Recipe, activate: bool) -> None:
        del activate
        QMessageBox.information(
            self,
            "Recipe saved",
            f"Recipe {recipe.recipe_number} — {recipe.name} revision {recipe.revision} "
            "was saved as a DRAFT.\n\n"
            "The accepted reference image and ROI configuration are now stored "
            "with this immutable revision.",
        )

    def activate_selected(self) -> None:
        recipe = self.selected_recipe()
        if recipe is None:
            return
        readiness = self.controller.inspection_readiness(recipe)
        if not readiness.get("ready"):
            QMessageBox.warning(
                self,
                "Inspection engine not ready",
                "This revision cannot be activated:\n\n"
                + "\n".join(str(item) for item in readiness.get("issues", [])),
            )
            return
        if recipe.validation_runs_passed < recipe.validation_runs_required:
            QMessageBox.warning(
                self,
                "Recipe not validated",
                "This recipe has not completed the required real validation runs.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Select recipe",
            f"Use {recipe.name} revision {recipe.revision} for manual triggers?\n\n"
            "PLC triggers are unaffected: the PLC names the product on every "
            "trigger and the station grades against the newest validated "
            "revision of the product it names.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.controller.activate_recipe(recipe)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Activation failed", str(exc))
            return
        self.recipe_activated.emit(recipe)

    def delete_recipe(self) -> None:
        recipe = self.selected_recipe()
        if recipe is None:
            return
        answer = QMessageBox.warning(
            self,
            "Delete recipe",
            f"Delete {recipe.name} and all of its revisions? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.controller.delete_recipe(recipe)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Delete failed", str(exc))

    def export_recipe(self) -> None:
        recipe = self.selected_recipe()
        if recipe is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export recipe",
            f"{recipe.name}_rev{recipe.revision}.json",
            "Recipe JSON (*.json)",
        )
        if not filename:
            return
        Path(filename).write_text(
            json.dumps(recipe.to_dict(), indent=2),
            encoding="utf-8",
        )

    def export_recipe_package(self) -> None:
        """Everything needed to run this revision on another machine."""

        recipe = self.selected_recipe()
        if recipe is None:
            return
        if recipe.reference_image is None or not recipe.reference_image.path:
            QMessageBox.warning(
                self,
                "Nothing to package",
                "This revision has no reference image, so there is nothing to move. "
                "Capture and accept a reference first.",
            )
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export recipe package",
            f"{recipe.name}_rev{recipe.revision}_package.zip",
            "Pole Position recipe package (*.zip)",
        )
        if not filename:
            return
        try:
            result = self.controller.export_recipe_package(recipe, Path(filename))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        manifest = result.get("manifest", {})
        carried_model = "with its bound ML model" if manifest.get("includes_model") else (
            "without an ML model: this station's installed model is not the one this "
            "revision is bound to, so install the right model on the destination"
        )
        QMessageBox.information(
            self,
            "Recipe package written",
            f"{recipe.name} revision {recipe.revision} was written {carried_model}.\n\n"
            "The package carries this station's reference image and validation "
            "evidence. Import it only onto a station with the same camera, lens, "
            "lighting, and fixture: nothing on the destination can check that for you.",
        )

    def import_recipe(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import recipe package or geometry",
            "",
            "Recipe package or geometry (*.zip *.json);;Pole Position recipe package (*.zip);;Recipe JSON (*.json)",
        )
        if not filename:
            return
        if filename.lower().endswith(".zip"):
            self._import_recipe_package(Path(filename))
            return
        try:
            imported = Recipe.from_dict(
                json.loads(Path(filename).read_text(encoding="utf-8"))
            )
            # Imported file paths are never trusted as station evidence. Build a
            # temporary revision-zero template and force a fresh station capture.
            template = Recipe.new(
                name=imported.name,
                recipe_number=0,
                part_number=imported.part_number,
                description=imported.description,
                created_by=self.controller.config.operator_name,
                battery_roi=replace(imported.battery_roi),
                terminals=[replace(item) for item in imported.terminals],
                orientation_reference=imported.orientation_reference,
                reference_image=None,
            )
            template.revision = 0
            template.status = RecipeStatus.DRAFT
            template.validation_runs_required = imported.validation_runs_required
            template.validation_runs_passed = 0
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        QMessageBox.information(
            self,
            "Imported geometry",
            "The imported geometry will open in the guided wizard. A fresh camera "
            "reference must be captured and accepted before the recipe can be saved.",
        )
        self._open_wizard(
            recipe=template,
            initial_reference_action="capture",
            template_mode=True,
        )

    def _import_recipe_package(self, source: Path) -> None:
        """Take a qualified recipe from another station, evidence and all.

        The technician is told what is being trusted before anything is written:
        which station recorded the evidence, when, how many samples, and whether
        the model it was validated against is the one installed here.
        """

        try:
            manifest = self.controller.inspect_recipe_package(source)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Import failed", str(exc))
            return

        matches = bool(manifest.get("model_matches_station"))
        model_line = (
            "The model it was validated against is the model installed here."
            if matches
            else (
                "WARNING: it was validated against a different ML model than this "
                "station has installed"
                + (
                    ". The package carries that model and importing will install it."
                    if manifest.get("includes_model")
                    else ", and the package does not carry that model. The recipe will "
                    "not be able to grade a part until that exact model is installed."
                )
            )
        )
        answer = QMessageBox.question(
            self,
            "Import recipe package",
            f"Import {manifest.get('recipe_name', '')} revision "
            f"{manifest.get('revision', '')} (recipe number "
            f"{manifest.get('recipe_number', '')})?\n\n"
            f"Exported from station: {manifest.get('source_station', '') or 'unnamed'}\n"
            f"Exported at: {manifest.get('created_at_utc', '')}\n"
            f"Validation: {manifest.get('validation_runs_passed', 0)}"
            f"/{manifest.get('validation_runs_required', 0)} samples, "
            f"complete={manifest.get('validation_complete')}\n\n"
            f"{model_line}\n\n"
            "The validation evidence was recorded on the exporting station's camera, "
            "lens, and lighting, and is imported as-is. Import only if this station "
            "is the same build. Run a known-good and a known-bad part before "
            "releasing it to production.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.controller.import_recipe_package(source)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        recipe = result["recipe"]
        self.set_recipes(self.controller.list_recipes())
        QMessageBox.information(
            self,
            "Recipe imported",
            f"{recipe.name} revision {recipe.revision} is on this station"
            + (" and its ML model was installed" if result.get("model") else "")
            + ".\n\nRun a known-good and a known-bad part before releasing it.",
        )

    def show_revisions(self) -> None:
        recipe = self.selected_recipe()
        if recipe is None:
            return
        revisions = self.controller.repository.list_revisions(recipe.recipe_id)
        lines = [
            f"Recipe {item.recipe_number}, revision {item.revision}: {item.status.value.upper()} — "
            f"updated by {item.updated_by} — "
            f"reference {'YES' if item.has_reference_image else 'NO'}"
            for item in revisions
        ]
        QMessageBox.information(
            self,
            f"{recipe.name} revisions",
            "\n".join(lines) or "No revisions found",
        )
