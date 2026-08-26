from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QIcon, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.controller import AppController
from battery_inspector.maintenance_passcode import verify
from battery_inspector.models import InspectionCycleStatus, InspectionResult, Recipe
from battery_inspector.ui_state import derive_run_state
from battery_inspector.ui.pages.diagnostics import DiagnosticsPage
from battery_inspector.ui.pages.events import EventsPage
from battery_inspector.ui.pages.inspection_detail import InspectionDetailPage
from battery_inspector.ui.pages.ml_training import MlTrainingPage
from battery_inspector.ui.pages.overview import OverviewPage
from battery_inspector.ui.pages.recipes import RecipesPage
from battery_inspector.ui.pages.settings import SettingsPage
from battery_inspector.ui.widgets import HealthItem, MetricCard, NavButton, StatusPill


class MainWindow(QMainWindow):
    OVERVIEW = 0
    INSPECTION = 1
    RECIPES = 2
    ML_TRAINING = 3
    DIAGNOSTICS = 4
    EVENTS = 5
    SETTINGS = 6

    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self.controller = controller
        self._busy = controller.busy
        self._maintenance_unlocked = False
        self._last_inspection = controller.last_inspection
        self._cycle_status = controller.cycle_status
        self.setWindowTitle("Pole Position — Battery Polarity Inspection")
        self.setWindowIcon(QIcon(str(controller.assets_directory / "app_icon.png")))
        self.resize(1600, 1000)
        self.setMinimumSize(1280, 760)

        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())

        middle = QWidget()
        middle_layout = QHBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)
        middle_layout.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        middle_layout.addWidget(self.stack, 1)
        layout.addWidget(middle, 1)

        self.overview_page = OverviewPage()
        self.inspection_page = InspectionDetailPage()
        self.recipes_page = RecipesPage(controller)
        self.ml_training_page = MlTrainingPage(controller)
        self.diagnostics_page = DiagnosticsPage(controller)
        self.events_page = EventsPage(controller)
        self.settings_page = SettingsPage(controller)
        for page in (
            self.overview_page,
            self.inspection_page,
            self.recipes_page,
            self.ml_training_page,
            self.diagnostics_page,
            self.events_page,
            self.settings_page,
        ):
            self.stack.addWidget(page)

        layout.addWidget(self._build_footer())

        self.overview_page.view_details_requested.connect(lambda: self.navigate(self.INSPECTION))
        self.overview_page.manual_inspection_requested.connect(self.request_manual_inspection)
        self.overview_page.production_counters_reset_requested.connect(
            self.request_production_counter_reset
        )
        self.overview_page.simulate_plc_trigger_requested.connect(self.simulate_plc_trigger)
        self.overview_page.bypass_toggle_requested.connect(self.request_bypass_change)
        self.inspection_page.back_requested.connect(lambda: self.navigate(self.OVERVIEW))
        self.recipes_page.recipe_activated.connect(lambda _recipe: self.navigate(self.OVERVIEW))

        controller.inspection_updated.connect(self.set_inspection)
        controller.active_recipe_changed.connect(self.set_active_recipe)
        controller.recipes_changed.connect(self.recipes_page.set_recipes)
        controller.health_changed.connect(self.set_health)
        controller.counts_changed.connect(self.set_counts)
        controller.busy_changed.connect(self.set_busy)
        controller.cycle_state_changed.connect(self.set_cycle_status)
        controller.configuration_changed.connect(self.set_configuration)
        controller.plc_simulation_state_changed.connect(self.overview_page.set_plc_simulation_state)
        controller.plc_simulation_state_changed.connect(lambda _state: self._refresh_run_state())
        controller.bypass_operation_failed.connect(self.bypass_operation_failed)
        controller.event_added.connect(lambda _event: self.events_page.refresh())

        self.set_counts(controller.counts_payload())
        self.set_health(controller.health)
        self.overview_page.set_plc_simulation_state(controller.plc_simulation_state())
        self.set_cycle_status(controller.cycle_status)
        self.recipes_page.set_recipes(controller.list_recipes())
        if controller.active_recipe:
            self.set_active_recipe(controller.active_recipe)
        self.navigate(self.OVERVIEW)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("Header")
        header.setFixedHeight(84)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(0)

        logo = QLabel()
        pixmap = QPixmap(str(self.controller.assets_directory / "app_icon.png"))
        logo.setPixmap(
            pixmap.scaled(
                58,
                58,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        layout.addWidget(logo)
        brand = QLabel("POLE\nPOSITION")
        brand.setObjectName("BrandTitle")
        brand.setMinimumWidth(126)
        layout.addWidget(brand)
        layout.addSpacing(16)

        self.run_state = StatusPill("RUNNING", "AUTO MODE")
        layout.addWidget(self.run_state)
        layout.addSpacing(14)

        self.active_recipe_metric = MetricCard("Recipe", "—")
        self.part_metric = MetricCard("Part count", "0")
        self.pass_metric = MetricCard("Pass", "0")
        self.fail_metric = MetricCard("Fail", "0")
        self.reject_metric = MetricCard("Reject rate", "0.0%")
        for metric in (
            self.active_recipe_metric,
            self.part_metric,
            self.pass_metric,
            self.fail_metric,
            self.reject_metric,
        ):
            layout.addWidget(metric)
        layout.addStretch(1)

        user_icon = QLabel("♙")
        user_icon.setStyleSheet("font-size: 23px;")
        layout.addWidget(user_icon)
        self.user_label = QLabel(self.controller.config.operator_name)
        self.user_label.setStyleSheet("font-size: 13px; margin-left: 6px;")
        layout.addWidget(self.user_label)
        return header

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(112)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        definitions = [
            (self.OVERVIEW, "⌂", "Overview"),
            (self.INSPECTION, "◎", "Inspection"),
            (self.RECIPES, "▣", "Recipes"),
            (self.ML_TRAINING, "ML", "ML Train"),
            (self.DIAGNOSTICS, "⌁", "Diagnostics"),
            (self.EVENTS, "△", "Events"),
            (self.SETTINGS, "⚙", "Settings"),
        ]
        self.nav_buttons: dict[int, NavButton] = {}
        for index, symbol, label in definitions:
            button = NavButton(symbol, label)
            button.clicked.connect(lambda _checked=False, i=index: self.navigate(i))
            self.nav_buttons[index] = button
            layout.addWidget(button)
        layout.addStretch(1)

        # Two separate things, and they were one. The button labelled Logout
        # closed the application, so a technician who had opened Settings and
        # wanted to hand the station back to an operator had no way to do that
        # short of shutting the HMI down and starting it again.
        logout = NavButton("↪", "Logout")
        logout.clicked.connect(self.log_out)
        layout.addWidget(logout)

        exit_button = NavButton("⏻", "Exit")
        exit_button.clicked.connect(self.request_close)
        layout.addWidget(exit_button)
        return sidebar

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("Footer")
        footer.setFixedHeight(52)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(10, 3, 12, 3)
        layout.setSpacing(0)
        self.health_items = {
            "camera": HealthItem("Camera"),
            "lighting": HealthItem("Lighting"),
            "plc": HealthItem("PLC"),
            "disk": HealthItem("Disk space"),
        }
        for item in self.health_items.values():
            item.clicked.connect(lambda: self.navigate(self.DIAGNOSTICS))
            layout.addWidget(item)
        layout.addStretch(1)
        self.system_health = HealthItem("System health")
        self.system_health.clicked.connect(lambda: self.navigate(self.DIAGNOSTICS))
        layout.addWidget(self.system_health)
        layout.addStretch(1)
        self.footer_user = QLabel(f"Current User: {self.controller.config.operator_name}")
        self.footer_user.setProperty("muted", True)
        layout.addWidget(self.footer_user)
        help_button = QPushButton("?  HELP")
        help_button.clicked.connect(self.show_help)
        layout.addSpacing(18)
        layout.addWidget(help_button)
        return footer

    # Screens where a wrong entry changes what the station inspects. Gating
    # them is a speed bump against a mis-tap, not a security control -- see
    # battery_inspector/maintenance_passcode.py.
    GATED_PAGES = (ML_TRAINING, SETTINGS)

    def prompt_for_passcode(self, screen: str) -> str | None:
        """Ask for the passcode. None means the technician cancelled.

        A separate method so the gate can be exercised without a modal dialog.
        """

        passcode, accepted = QInputDialog.getText(
            self,
            f"{screen} is protected",
            f"Enter the maintenance passcode to open {screen}.",
            QLineEdit.EchoMode.Password,
        )
        return passcode if accepted else None

    def report_passcode_refused(self, screen: str) -> None:
        """Say the screen did not open. Separate, so it can be silenced in tests."""

        QMessageBox.warning(self, "Passcode not accepted", f"{screen} was not opened.")

    def unlock_maintenance_screens(self) -> None:
        """Treat the maintenance screens as already unlocked."""

        self._maintenance_unlocked = True

    def _screen_is_unlocked(self, index: int) -> bool:
        if index not in self.GATED_PAGES:
            return True
        if self._maintenance_unlocked:
            return True

        name = {self.ML_TRAINING: "ML Training", self.SETTINGS: "Settings"}[index]
        passcode = self.prompt_for_passcode(name)
        if passcode is None:
            return False
        if not verify(
            passcode,
            self.controller.config.maintenance_passcode_salt,
            self.controller.config.maintenance_passcode_hash,
        ):
            # Recorded, because "who was in Settings before that recipe
            # changed" is a question the audit log has to be able to answer,
            # and so does "who was trying to be".
            self.controller.record_maintenance_access(name, granted=False)
            self.report_passcode_refused(name)
            return False

        self._maintenance_unlocked = True
        self.controller.record_maintenance_access(name, granted=True)
        return True

    def lock_maintenance_screens(self) -> None:
        """Require the passcode again for the gated screens."""

        self._maintenance_unlocked = False

    def log_out(self) -> None:
        """Hand the station back to an operator, without stopping inspection.

        Locks the maintenance screens and returns to Overview. The application
        keeps running: a station that stops inspecting because a technician
        finished in Settings would be a worse outcome than leaving those
        screens unlocked.
        """

        was_unlocked = self._maintenance_unlocked
        self.lock_maintenance_screens()
        if was_unlocked:
            self.controller.record_maintenance_access("Maintenance screens", granted=False)
        self.navigate(self.OVERVIEW)

    def page_at(self, index: int) -> QWidget:
        return self.stack.widget(index)

    def current_page(self) -> QWidget:
        return self.stack.currentWidget()

    def navigate(self, index: int) -> None:
        if not self._screen_is_unlocked(index):
            # Leave the sidebar showing the page actually on screen, not the
            # one that was refused.
            for button_index, button in self.nav_buttons.items():
                button.setChecked(button_index == self.stack.currentIndex())
            return
        self.stack.setCurrentIndex(index)
        for button_index, button in self.nav_buttons.items():
            button.setChecked(button_index == index)
        if index == self.EVENTS:
            self.events_page.refresh()
        elif index == self.ML_TRAINING:
            self.ml_training_page.refresh_counts()

    def set_active_recipe(self, recipe: Recipe) -> None:
        self.active_recipe_metric.set_value(
            f"{recipe.recipe_number} — {recipe.name}"
        )
        self.overview_page.set_recipe_geometry(
            recipe.recipe_id,
            recipe.battery_roi,
            recipe.terminals,
        )
        self.inspection_page.set_recipe(recipe.terminals)

    def set_inspection(self, result: InspectionResult) -> None:
        self._last_inspection = result
        # The PLC names the product on every trigger, so the recipe shown in the
        # header is whatever actually graded the last part -- not a station
        # selection that may have had nothing to do with it.
        if result.is_product_result and result.recipe_name:
            self.active_recipe_metric.set_value(result.recipe_name)
        self.overview_page.set_inspection(result)
        self.inspection_page.set_inspection(result)
        self._refresh_run_state()

    def set_counts(self, counts: dict) -> None:
        part_count = int(counts["part_count"])
        pass_count = int(counts["pass_count"])
        fail_count = int(counts["fail_count"])
        reject_rate = float(counts["reject_rate"])
        self.part_metric.set_value(str(part_count))
        # Normal production values remain neutral; exceptions receive color.
        self.pass_metric.set_value(str(pass_count))
        self.fail_metric.set_value(str(fail_count), "bad" if fail_count else None)
        self.reject_metric.set_value(
            f"{reject_rate:.1f}%",
            "warning" if reject_rate > 0.0 else None,
        )
        self.overview_page.set_recent_results(counts["recent"])
        self.overview_page.set_production_count(part_count)

    def set_health(self, health: dict) -> None:
        for key, item in self.health_items.items():
            state = health.get(key, {"ok": False, "text": "UNKNOWN"})
            item.set_state(bool(state["ok"]), str(state["text"]))
        system = health.get("system", {"ok": False, "text": "UNKNOWN"})
        self.system_health.set_state(bool(system["ok"]), str(system["text"]))
        self.diagnostics_page.set_health(health)
        self.overview_page.set_plc_health(
            health.get("plc", {"ok": False, "text": "UNKNOWN"})
        )
        self._refresh_run_state()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.overview_page.set_busy(busy, self.controller.busy_reason)
        self._refresh_run_state()

    def set_cycle_status(self, payload: object) -> None:
        if not isinstance(payload, InspectionCycleStatus):
            return
        self._cycle_status = payload
        self.overview_page.set_cycle_status(payload)
        self._refresh_run_state()

    def set_configuration(self, config) -> None:
        self.user_label.setText(config.operator_name)
        self.footer_user.setText(f"Current User: {config.operator_name}")
        self._refresh_run_state()

    def _refresh_run_state(self) -> None:
        system_ok = bool(self.controller.health.get("system", {}).get("ok"))
        system_text = str(self.controller.health.get("system", {}).get("text", ""))
        presentation = derive_run_state(
            busy=self._busy,
            busy_reason=self.controller.busy_reason,
            system_ok=system_ok,
            plc_simulation=self.controller.plc_simulation_active,
            last_result_passed=(
                self._last_inspection.passed
                if self._last_inspection is not None and self._last_inspection.is_product_result
                else None
            ),
            last_disposition=(
                self._last_inspection.disposition if self._last_inspection is not None else None
            ),
            system_text=system_text,
            cycle_state=self._cycle_status.state,
            cycle_message=self._cycle_status.message,
        )
        if self.controller.bypass_active and not self._busy:
            self.run_state.set_state(
                "warning",
                "BYPASS",
                "INSPECTION INTERLOCK BYPASSED",
            )
            return
        self.run_state.set_state(
            presentation.tone,
            presentation.title,
            presentation.subtitle,
        )

    def request_manual_inspection(self) -> None:
        if self.controller.run_inspection("MANUAL"):
            return
        QMessageBox.information(
            self,
            "Camera is occupied",
            "The camera is currently acquiring, applying settings, or capturing a recipe reference. "
            "Wait for the active operation to finish and trigger again.",
        )

    def request_production_counter_reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset production counters?",
            "Reset the session Part, Pass, Fail, reject-rate, and recent-result "
            "counters to zero?\n\n"
            "This does not delete retained failure evidence, recipes, models, "
            "validation records, or the last displayed inspection.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.controller.reset_production_counters():
            return
        QMessageBox.information(
            self,
            "Counters not reset",
            "The station must be idle before production counters can be reset.",
        )

    def simulate_plc_trigger(self) -> None:
        if not self.controller.pulse_simulated_plc_trigger():
            QMessageBox.information(
                self,
                "PLC Simulation is not active",
                "Enable PLC Simulation first, then send the test trigger.",
            )

    def request_bypass_change(self, enabled: bool) -> None:
        if enabled:
            answer = QMessageBox.warning(
                self,
                "Enable inspection bypass?",
                "BYPASS is an abnormal operating mode. Inspection continues: the HMI will acquire, inspect, "
                "record, and display the actual inspection result, but the configured PLC bypass tag "
                "will be set TRUE so PLC logic can ignore the inspection interlock.\n\n"
                "Enable bypass now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if self.controller.request_bypass(enabled):
            return
        QMessageBox.information(
            self,
            "Bypass change not accepted",
            "The PLC must be connected and the station must be idle before bypass can be changed. "
            "Wait for the current inspection/PLC operation to finish and try again.",
        )

    def bypass_operation_failed(self, message: str) -> None:
        QMessageBox.critical(
            self,
            "PLC bypass change failed",
            f"The bypass request was not confirmed by the PLC.\n\n{message}",
        )

    def show_help(self) -> None:
        QMessageBox.information(
            self,
            "Pole Position help",
            "Overview shows the last inspection. Inspection shows exact terminal and marking crops. "
            "Recipes opens the guided low-level technician workflow. "
            "Diagnostics contains camera, PLC, and vision status.",
        )

    def request_close(self) -> None:
        answer = QMessageBox.question(self, "Exit Pole Position", "Exit the HMI application?")
        if answer == QMessageBox.StandardButton.Yes:
            self.close()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self.controller.shutdown()
        event.accept()
