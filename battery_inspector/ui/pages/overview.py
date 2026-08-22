from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from battery_inspector.models import (
    InspectionCycleStatus,
    InspectionDisposition,
    InspectionResult,
)
from battery_inspector.services.vision import rect_within
from battery_inspector.ui.image_widgets import (
    ImageOverlayWidget,
    OverlaySpec,
    PolygonOverlaySpec,
)
from battery_inspector.ui.palette import (
    AMBER_BG,
    BORDER_LIGHT,
    ROI_AUXILIARY,
    ROI_BATTERY,
    ROI_MARKING,
    ROLE_NEGATIVE,
    ROLE_POSITIVE,
    TEXT,
    TEXT_MUTED,
)
from battery_inspector.ui.widgets import (
    AMBER,
    BAD,
    GOOD,
    LabeledValue,
    PanelFrame,
    RecentResults,
    ResultBadge,
)


class OverviewPage(QWidget):
    view_details_requested = Signal()
    manual_inspection_requested = Signal()
    production_counters_reset_requested = Signal()
    simulate_plc_trigger_requested = Signal()
    bypass_toggle_requested = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(10)

        toolbar = QHBoxLayout()
        title = QLabel("OVERVIEW")
        title.setObjectName("PageTitle")
        toolbar.addWidget(title)
        toolbar.addSpacing(18)
        self.cycle_status_label = QLabel("READY FOR TRIGGER")
        self.cycle_status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 14px; font-weight: 800;"
        )
        toolbar.addWidget(self.cycle_status_label)
        toolbar.addStretch(1)
        self.reset_counters_button = QPushButton("RESET PRODUCTION COUNTERS")
        self.reset_counters_button.setToolTip(
            "Clears only the session Part, Pass, Fail, reject-rate, and recent-result "
            "counters. Inspection evidence, recipes, models, and validation are unchanged."
        )
        self.reset_counters_button.clicked.connect(
            self.production_counters_reset_requested
        )
        self.reset_counters_button.setEnabled(False)
        toolbar.addWidget(self.reset_counters_button)
        toolbar.addSpacing(8)
        self.bypass_button = QPushButton("ENABLE BYPASS")
        self.bypass_button.setToolTip(
            "Sets the configured PLC bypass tag. Inspection continues and results are logged; "
            "PLC logic decides whether the inspection interlock is enforced."
        )
        self.bypass_button.clicked.connect(self._request_bypass_toggle)
        toolbar.addWidget(self.bypass_button)
        toolbar.addSpacing(8)
        self.run_button = QPushButton("RUN MANUAL INSPECTION")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self.manual_inspection_requested)
        toolbar.addWidget(self.run_button)
        root.addLayout(toolbar)

        plc_strip = PanelFrame(subpanel=True)
        plc_layout = QHBoxLayout(plc_strip)
        plc_layout.setContentsMargins(12, 8, 12, 8)
        plc_title = QLabel("PLC CYCLE")
        plc_title.setObjectName("SectionTitle")
        plc_layout.addWidget(plc_title)
        self.plc_mode_text = QLabel("CONNECTING")
        self.plc_mode_text.setStyleSheet(f"color: {AMBER}; font-weight: 800;")
        plc_layout.addSpacing(12)
        plc_layout.addWidget(self.plc_mode_text)
        self.plc_cycle_text = QLabel(
            "Trigger OFF  |  Busy OFF  |  Complete OFF  |  Result NONE"
        )
        self.plc_cycle_text.setProperty("muted", True)
        plc_layout.addSpacing(18)
        plc_layout.addWidget(self.plc_cycle_text, 1)
        self.simulate_plc_trigger_button = QPushButton("SEND TEST PLC TRIGGER")
        self.simulate_plc_trigger_button.clicked.connect(
            self.simulate_plc_trigger_requested
        )
        self.simulate_plc_trigger_button.setEnabled(False)
        plc_layout.addWidget(self.simulate_plc_trigger_button)
        root.addWidget(plc_strip)

        self._plc_simulation_active = False
        self._station_busy = False
        self._counter_has_data = False
        self._bypass_active = False
        self._bypass_known = False
        self._bypass_pending = False
        self._recipe_terminal_overlays: list[OverlaySpec] = []
        self._geometry_recipe_id = ""

        content = QHBoxLayout()
        content.setSpacing(10)
        root.addLayout(content, 1)

        image_panel = PanelFrame()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(14, 12, 14, 12)
        image_layout.setSpacing(8)
        panel_title = QLabel("LAST INSPECTION — EXACT ACQUIRED FRAME")
        panel_title.setObjectName("PanelTitle")
        image_layout.addWidget(panel_title)

        self.image = ImageOverlayWidget()
        self.image.setMinimumSize(560, 340)
        image_layout.addWidget(self.image, 1)

        recent_header = QHBoxLayout()
        recent_label = QLabel("RECENT PRODUCT RESULTS")
        recent_label.setObjectName("SectionTitle")
        recent_header.addWidget(recent_label)
        recent_header.addStretch(1)
        image_layout.addLayout(recent_header)
        self.recent = RecentResults()
        image_layout.addWidget(self.recent)
        content.addWidget(image_panel, 4)

        result_panel = PanelFrame()
        result_panel.setMinimumWidth(305)
        result_panel.setMaximumWidth(420)
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(16, 14, 16, 14)
        result_layout.setSpacing(7)
        result_label = QLabel("OVERALL RESULT")
        result_label.setObjectName("SectionTitle")
        result_layout.addWidget(result_label)

        self.result_badge = ResultBadge()
        result_layout.addWidget(self.result_badge)

        self.reason = LabeledValue("Reason")
        self.cycle = LabeledValue("Cycle")
        self.captured = LabeledValue("Captured")
        self.frame = LabeledValue("Frame / resolution")
        self.duration = LabeledValue("Inspection time")
        self.trigger = LabeledValue("Trigger source")
        self.quality = LabeledValue("Image quality")
        for item in (
            self.reason,
            self.cycle,
            self.captured,
            self.frame,
            self.duration,
            self.trigger,
            self.quality,
        ):
            result_layout.addWidget(item)
            divider = QFrame()
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setStyleSheet(f"color: {BORDER_LIGHT};")
            result_layout.addWidget(divider)

        result_layout.addStretch(1)
        self.details_button = QPushButton("VIEW DETAILS   →")
        self.details_button.clicked.connect(self.view_details_requested)
        self.details_button.setEnabled(False)
        result_layout.addWidget(self.details_button)
        content.addWidget(result_panel, 1)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_busy(self, busy: bool, activity: str = "") -> None:
        self._station_busy = busy
        self.run_button.setDisabled(busy)
        normalized = activity.strip().upper()
        if not busy:
            self.run_button.setText("RUN MANUAL INSPECTION")
        elif normalized in {"ACQUIRING", "LOCATING", "INSPECTING", "SAVING"}:
            self.run_button.setText(f"{normalized}…")
        else:
            self.run_button.setText("PLEASE WAIT…")
        self.simulate_plc_trigger_button.setEnabled(
            self._plc_simulation_active and not busy
        )
        self.bypass_button.setEnabled(
            (not busy) and self._bypass_known and not self._bypass_pending
        )
        self.reset_counters_button.setEnabled(
            self._counter_has_data and not busy
        )

    def set_cycle_status(self, status: InspectionCycleStatus) -> None:
        if status.state.active:
            text = f"{status.state.display} — {status.message}"
            color = ROI_BATTERY
        elif status.state.value == "complete":
            text = f"COMPLETE — {status.message}"
            color = GOOD
        elif status.state.value == "not_ready":
            text = f"NOT READY — {status.message}"
            color = AMBER
        elif status.state.value == "fault":
            text = f"FAULT — {status.message}"
            color = BAD
        else:
            text = status.message or "READY FOR TRIGGER"
            color = TEXT_MUTED
        self.cycle_status_label.setText(text)
        self.cycle_status_label.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: 800;"
        )

    def set_plc_health(self, state: dict) -> None:
        text = str(state.get("text", "UNKNOWN"))
        ok = bool(state.get("ok"))
        if text == "SIMULATION":
            color = AMBER
        else:
            color = TEXT if ok else BAD
        self.plc_mode_text.setText(text)
        self.plc_mode_text.setStyleSheet(f"color: {color}; font-weight: 800;")

    def set_plc_simulation_state(self, payload: object) -> None:
        state = dict(payload)  # type: ignore[arg-type]
        active = bool(state.get("active"))
        self._plc_simulation_active = active
        self.simulate_plc_trigger_button.setEnabled(active and not self._station_busy)

        self._bypass_active = bool(state.get("bypass", False))
        self._bypass_known = bool(state.get("bypass_known", False))
        self._bypass_pending = bool(state.get("bypass_pending", False))
        self._update_bypass_button()

        passed = state.get("passed")
        failed = bool(state.get("fail", False))
        complete = bool(state.get("complete"))
        if passed is True:
            result = "PASS"
        elif failed and complete:
            result = "FAIL"
        else:
            result = "NONE"
        heartbeat = "1" if state.get("heartbeat") else "0"
        bypass_text = "ACTIVE" if self._bypass_active else ("OFF" if self._bypass_known else "UNKNOWN")
        self.plc_cycle_text.setText(
            f"HB {heartbeat}  |  Bypass {bypass_text}  |  "
            f"Trigger {'ON' if state.get('trigger') else 'OFF'}  |  "
            f"Busy {'ON' if state.get('busy') else 'OFF'}  |  "
            f"Complete {'ON' if complete else 'OFF'}  |  Result {result}"
        )

    def _request_bypass_toggle(self) -> None:
        if not self._bypass_known or self._bypass_pending:
            return
        self.bypass_toggle_requested.emit(not self._bypass_active)

    def _update_bypass_button(self) -> None:
        if self._bypass_pending:
            self.bypass_button.setText("UPDATING BYPASS…")
            self.bypass_button.setEnabled(False)
            self.bypass_button.setStyleSheet(
                f"color: {AMBER}; background: {AMBER_BG}; border: 2px solid {AMBER}; font-weight: 800;"
            )
            return
        if not self._bypass_known:
            self.bypass_button.setText("BYPASS UNKNOWN")
            self.bypass_button.setEnabled(False)
            self.bypass_button.setStyleSheet(
                f"color: {BAD}; border: 2px solid {BAD}; font-weight: 800;"
            )
            return
        if self._bypass_active:
            self.bypass_button.setText("BYPASS ACTIVE")
            self.bypass_button.setEnabled(not self._station_busy)
            self.bypass_button.setStyleSheet(
                f"color: {AMBER}; background: {AMBER_BG}; border: 2px solid {AMBER}; font-weight: 900;"
            )
        else:
            self.bypass_button.setText("ENABLE BYPASS")
            self.bypass_button.setEnabled(not self._station_busy)
            self.bypass_button.setStyleSheet("")

    def set_recent_results(self, values: list[bool]) -> None:
        self.recent.set_results(values)

    def set_production_count(self, part_count: int) -> None:
        self._counter_has_data = int(part_count) > 0
        self.reset_counters_button.setEnabled(
            self._counter_has_data and not self._station_busy
        )

    @staticmethod
    def _format_timestamp(value: str) -> str:
        if not value:
            return "—"
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return stamp.astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        except ValueError:
            return value

    @staticmethod
    def _tone_for_disposition(disposition: InspectionDisposition) -> str:
        if disposition == InspectionDisposition.PASS:
            return "good"
        if disposition in {
            InspectionDisposition.NOT_READY,
            InspectionDisposition.INDETERMINATE,
        }:
            return "warning"
        return "bad"

    def set_inspection(self, result: InspectionResult) -> None:
        image_path = Path(result.full_image_path) if result.full_image_path else None
        image_exists = bool(image_path and image_path.is_file())
        image_available = result.full_image is not None or image_exists
        if result.full_image is not None:
            self.image.set_array(result.full_image)
        else:
            self.image.set_image(image_path if image_exists else None)

        overlays: list[OverlaySpec] = []
        polygon_overlays: list[PolygonOverlaySpec] = []
        if image_available and result.recipe_id:
            if result.battery_polygon:
                polygon_overlays.append(
                    PolygonOverlaySpec(
                        key="battery",
                        points=result.battery_polygon,
                        label="REGISTERED BATTERY",
                        color=ROI_BATTERY,
                        dashed=False,
                        line_width=3,
                    )
                )
                role_colors = {
                    "negative": ROLE_NEGATIVE,
                    "positive": ROLE_POSITIVE,
                    "auxiliary": ROI_AUXILIARY,
                }
                for index, terminal in enumerate(result.terminals, start=1):
                    color = role_colors.get(terminal.role.value, ROI_AUXILIARY)
                    if terminal.terminal_polygon:
                        polygon_overlays.append(
                            PolygonOverlaySpec(
                                key=f"{terminal.terminal_key}-search",
                                points=terminal.terminal_polygon,
                                label=f"{index} {terminal.role.display}",
                                color=color,
                                line_width=3,
                            )
                        )
                    if terminal.marking_polygon:
                        polygon_overlays.append(
                            PolygonOverlaySpec(
                                key=f"{terminal.terminal_key}-marking",
                                points=terminal.marking_polygon,
                                label="MARKING ROI",
                                color=ROI_MARKING,
                                dashed=True,
                                line_width=2,
                            )
                        )
            else:
                overlays.append(
                    OverlaySpec(
                        key="battery",
                        rect=result.battery_roi,
                        label="TAUGHT BATTERY AREA" if not result.analysis_ready else "BATTERY",
                        color=ROI_BATTERY,
                        dashed=not result.analysis_ready,
                        line_width=2,
                    )
                )
            if not result.battery_polygon and result.recipe_id == self._geometry_recipe_id:
                overlays.extend(self._recipe_terminal_overlays)
        self.image.set_overlays(overlays)
        self.image.set_polygon_overlays(polygon_overlays)

        self.result_badge.set_disposition(result.disposition)
        tone = self._tone_for_disposition(result.disposition)
        self.reason.set_value(result.reason, tone)
        self.cycle.set_value(result.cycle_id or "—")
        self.captured.set_value(self._format_timestamp(result.captured_at_utc))
        frame_text = (
            f"{result.frame_id or '—'}\n"
            f"{result.frame_width} × {result.frame_height} × {result.frame_channels}"
            if result.frame_width and result.frame_height
            else (result.frame_id or "—")
        )
        self.frame.set_value(frame_text)
        self.duration.set_value(f"{result.duration_ms} ms")
        self.trigger.set_value(result.trigger_source)
        quality_tone = (
            "good"
            if result.image_quality == "GOOD"
            else "warning" if result.image_quality in {"WARNING", "UNKNOWN"} else "bad"
        )
        self.quality.set_value(result.image_quality, quality_tone)
        self.details_button.setEnabled(True)

    def set_recipe_geometry(self, recipe_id: str, battery_roi, terminals) -> None:
        self._geometry_recipe_id = recipe_id
        role_colors = {
            "negative": ROLE_NEGATIVE,
            "positive": ROLE_POSITIVE,
            "auxiliary": ROI_AUXILIARY,
        }
        self._recipe_terminal_overlays = []
        for index, terminal in enumerate(terminals, start=1):
            self._recipe_terminal_overlays.append(
                OverlaySpec(
                    key=terminal.key,
                    rect=rect_within(battery_roi, terminal.search_roi),
                    label=f"{index}  {terminal.role.display} ROI",
                    color=role_colors[terminal.role.value],
                    line_width=3,
                )
            )
