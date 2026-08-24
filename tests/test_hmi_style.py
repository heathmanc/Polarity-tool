from __future__ import annotations

import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "battery_inspector" / "ui"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_controlled_theme_is_light_and_has_no_legacy_dark_palette() -> None:
    theme = _read("battery_inspector/ui/theme.qss")
    assert "background: #D8DCDE" in theme
    assert "background: #F7F8F8" in theme
    assert "color: #1D2429" in theme

    legacy_dark_colors = {
        "#071018",
        "#08121A",
        "#09131B",
        "#0A141C",
        "#0D1821",
        "#10171C",
        "#101A21",
        "#241010",
        "#241F10",
    }
    upper_theme = theme.upper()
    for color in legacy_dark_colors:
        assert color not in upper_theme


def test_pole_position_brand_and_icon_are_wired_into_the_hmi() -> None:
    main_window = _read("battery_inspector/ui/main_window.py")
    application = _read("battery_inspector/main.py")
    assert 'self.setWindowTitle("Pole Position — Battery Polarity Inspection")' in main_window
    assert 'brand = QLabel("POLE\\nPOSITION")' in main_window
    assert 'app.setApplicationName("Pole Position")' in application
    assert 'app.setApplicationDisplayName("Pole Position")' in application
    assert 'app.setWindowIcon(QIcon(' in application
    assert "Battery Inspector help" not in main_window
    assert "Exit Battery Inspector" not in main_window

    package = _read("pyproject.toml")
    assert 'name = "pole-position-hmi"' in package
    assert 'pole-position = "battery_inspector.main:main"' in package
    assert 'battery-inspector = "battery_inspector.main:main"' in package

    png = (ROOT / "battery_inspector/assets/app_icon.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (512, 512)
    # PNG color type 6 is true color with alpha; the transparent icon must not
    # gain a rectangular background when shown in the HMI header.
    assert png[25] == 6

    ico = (ROOT / "battery_inspector/assets/app_icon.ico").read_bytes()
    reserved, icon_type, image_count = struct.unpack("<HHH", ico[:6])
    assert reserved == 0
    assert icon_type == 1
    assert image_count >= 6


def test_scrolling_exists_only_as_the_sanctioned_overflow_container() -> None:
    """Station pages fit and paginate. Exactly one exception is allowed.

    The rule this replaces forbade QScrollArea outright, and it was the right
    instinct: an operator should never have to hunt for a control that is off
    screen. It could not hold in practice. The window's minimum is shorter than
    several pages need, and Windows display scaling makes the workspace smaller
    still -- a 4K panel at 150% reports 1280x720. A layout denied the room it
    needs does not refuse; it compresses its children until wrapped text and
    image panels draw over each other, which is worse than a scroll bar and far
    harder to notice.

    So scrolling is permitted only through VerticalScrollArea, only as the
    container MainWindow puts every page behind, and it shows nothing at all
    when the page fits -- which on a correctly specified station is always.
    Pages themselves still must not scroll their own content: a page that needs
    to scroll on a normal workspace is a page to redesign, and
    test_no_page_is_compressed_at_any_workspace_size measures that directly.
    """

    for path in sorted(UI.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "QScrollArea" not in source and "VerticalScrollArea" not in source:
            continue
        if path.name == "widgets.py":
            continue
        assert "QScrollArea" not in source, (
            f"{path.name} builds its own scroll area; use VerticalScrollArea"
        )
        assert path.name == "main_window.py", (
            f"{path.name} scrolls its own content; only MainWindow may scroll a page"
        )


def test_scrollbar_chrome_is_suppressed() -> None:
    theme = _read("battery_inspector/ui/theme.qss")
    assert re.search(r"QScrollBar:vertical\s*\{[^}]*width:\s*0px", theme, re.S)
    assert re.search(r"QScrollBar:horizontal\s*\{[^}]*height:\s*0px", theme, re.S)


def test_long_tables_use_pagination_instead_of_scrollbars() -> None:
    for page in (
        "battery_inspector/ui/pages/recipes.py",
        "battery_inspector/ui/pages/events.py",
    ):
        source = _read(page)
        assert "PageNavigator" in source
        assert "ScrollBarAlwaysOff" in source
        assert "previous_requested" in source
        assert "next_requested" in source


def test_inspection_detail_uses_stacked_terminal_pages() -> None:
    source = _read("battery_inspector/ui/pages/inspection_detail.py")
    assert "QStackedWidget" in source
    assert "PageNavigator(\"TERMINAL\")" in source
    assert "QScrollArea" not in source


def test_settings_use_fixed_tabs_not_a_scrolling_page() -> None:
    source = _read("battery_inspector/ui/pages/settings.py")
    for tab_label in (
        "GENERAL",
        "CAMERA DEVICE",
        "CAMERA IMAGE",
        "CAMERA I/O",
        "VISION / ML",
        "PLC MODE",
        "PLC TAGS",
    ):
        assert tab_label in source
    assert "setUsesScrollButtons(False)" in source
    assert "QScrollArea" not in source


def test_plc_save_does_not_validate_untouched_stale_ml_fields() -> None:
    source = _read("battery_inspector/ui/pages/settings.py")
    assert "controller.ml_model_changed.connect(self.ml_model_configuration_changed)" in source
    assert "user_edited=self._ml_settings_touched" in source
    assert "self.ml_model_path.setText(configured.model_path)" in source
    assert "self.ml_manifest_path.setText(configured.manifest_path)" in source


def test_plc_mode_trigger_and_recipe_selector_have_one_authoritative_ui_path() -> None:
    settings = _read("battery_inspector/ui/pages/settings.py")
    overview = _read("battery_inspector/ui/pages/overview.py")

    assert 'general_form.addRow("PLC source"' not in settings
    assert "plc_fallback" not in settings
    assert "enable_plc_simulation_button" not in settings
    assert "enable_plc_simulation_button" not in overview
    assert 'self.trigger_mode.addItem("Continuous / free run"' not in settings
    assert 'self.trigger_source.addItem("Software"' not in settings
    assert "PLC tag —" in settings
    assert "Recipe selector value" in settings
    assert "Recipe number — SINT / INT / DINT value" in settings


def test_recipe_page_and_wizard_expose_stable_recipe_numbers() -> None:
    recipes = _read("battery_inspector/ui/pages/recipes.py")
    wizard = _read("battery_inspector/ui/wizard/recipe_wizard.py")

    assert '["NO.", "RECIPE NAME"' in recipes
    assert 'LabeledValue("Recipe number")' in recipes
    assert 'form.addRow("Recipe number", self.recipe_number)' in wizard
    assert "number_locked=recipe is not None" in wizard


def test_recipe_wizard_uses_non_alarm_role_colors() -> None:
    source = _read("battery_inspector/ui/wizard/recipe_wizard.py")
    assert "ROLE_NEGATIVE" in source
    assert "ROLE_POSITIVE" in source
    assert "ROI_MARKING" in source
    assert "color = GOOD if terminal.role" not in source
    assert "color = BAD if key == \"negative\"" not in source


def test_palette_separates_role_colors_from_alarm_colors() -> None:
    namespace: dict[str, str] = {}
    exec(_read("battery_inspector/ui/palette.py"), namespace)
    assert namespace["ROLE_NEGATIVE"] not in {namespace["BAD"], namespace["GOOD"]}
    assert namespace["ROLE_POSITIVE"] not in {namespace["BAD"], namespace["GOOD"]}
    assert namespace["ROI_MARKING"] not in {namespace["BAD"], namespace["GOOD"]}


def test_ui_contract_declares_light_scrollbar_free_hmi() -> None:
    contract = _read("docs/UI_CONTRACT.md").lower()
    assert "there is no dark theme" in contract
    assert "no scroll bars" in contract
    assert "product rejects and system faults are separate" in contract


def test_ml_training_is_a_guided_scrollbar_free_hmi_page() -> None:
    source = _read("battery_inspector/ui/pages/ml_training.py")
    main = _read("battery_inspector/ui/main_window.py")
    assert "ML TRAINING" in source
    assert "CAPTURE FRESH FRAME" in source
    assert "SAVE ALL CIRCLES" in source
    assert "+ ADD CIRCLE" in source
    assert "CAPTURE MULTIPLE TERMINAL TOPS FROM ONE FRAME" in source
    assert "ACTIVE ROI — EXACT ML INPUT" not in source
    assert "self.crop_preview" not in source
    assert "targets are guidance only" in source.lower()
    assert "crop_confirm" not in source
    assert "I verified this crop excludes the red ring" not in source
    assert "REVIEW / CORRECT TRAINING DATA" in source
    assert 'PageNavigator("DATA PAGE")' in source
    assert "Correct training label" in source
    assert "Remove training image" in source
    assert "PREPARE DATASET" in source
    assert "START MODEL TRAINING" in source
    assert "INSTALL CANDIDATE FOR RECIPE VALIDATION" in source
    assert "verify_ml_training_candidate" in source
    assert "per_class_ready" not in source
    assert "RoiEditor" in source
    assert "QScrollArea" not in source
    assert "MlTrainingPage" in main
    assert '"ML Train"' in main


def test_overview_exposes_isa_style_bypass_control_and_plc_status() -> None:
    overview = _read("battery_inspector/ui/pages/overview.py")
    main = _read("battery_inspector/ui/main_window.py")
    assert "ENABLE BYPASS" in overview
    assert "BYPASS ACTIVE" in overview
    assert "BYPASS UNKNOWN" in overview
    assert "bypass_toggle_requested" in overview
    assert "HB {heartbeat}" in overview
    assert "Inspection interlock bypassed".upper() in main.upper()
    assert "inspection continues" in main.lower()


def test_overview_exposes_confirmed_session_counter_reset() -> None:
    overview = _read("battery_inspector/ui/pages/overview.py")
    main = _read("battery_inspector/ui/main_window.py")
    controller = _read("battery_inspector/controller.py")

    assert "RESET PRODUCTION COUNTERS" in overview
    assert "production_counters_reset_requested" in overview
    assert "Reset production counters?" in main
    assert "does not delete retained failure evidence" in main.lower()
    assert "def reset_production_counters" in controller
    assert "self.recent_results.clear()" in controller
    assert "if self.busy:" in controller


def test_memory_first_pass_and_binary_plc_contract_are_visible_in_source() -> None:
    vision = _read("battery_inspector/services/vision.py")
    controller = _read("battery_inspector/controller.py")
    repository = _read("battery_inspector/data/repository.py")
    plc = _read("battery_inspector/services/plc.py")

    assert "retain_evidence = validation_mode or not result.passed" in vision
    assert "Finalizing PASS in memory — no evidence retained" in vision
    assert "if not result.passed:" in controller
    assert 'disposition == "pass" and trigger_source != "RECIPE_VALIDATION"' in repository
    assert "self.tags.fail" in plc
    assert "fail_code" not in plc
