import json
from pathlib import Path

from battery_inspector.config import (
    AppConfig,
    MlConfig,
    ml_configuration_requires_apply,
)


def test_plc_mode_is_explicit_and_recipe_selector_defaults_to_name() -> None:
    config = AppConfig.default()
    assert config.plc_backend == "simulation"
    assert config.plc_recipe_selector == "name"


def test_existing_configuration_ignores_removed_fallback_setting(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "camera_backend": "auto",
                "plc_backend": "pycomm3",
                "plc_fallback_to_simulation": True,
                "camera": {},
                "plc_address": "192.168.1.10/1",
            }
        ),
        encoding="utf-8",
    )

    config = AppConfig.load(path)
    assert config.plc_backend == "pycomm3"
    assert not hasattr(config, "plc_fallback_to_simulation")


def test_recipe_selector_type_round_trips(tmp_path) -> None:
    path = tmp_path / "config.json"
    config = AppConfig(plc_recipe_selector="number")
    config.save(path)

    assert AppConfig.load(path).plc_recipe_selector == "number"


def test_camera_trigger_profile_is_normalized_to_plc_requested_free_run() -> None:
    from battery_inspector.config import CameraConfig

    normalized = CameraConfig(trigger_mode="On", trigger_source="Line1").normalized()

    assert normalized.trigger_mode == "Off"
    assert normalized.trigger_source == "Software"


def test_ml_configuration_defaults_to_standard_model_package() -> None:
    config = AppConfig.default()
    assert config.ml.model_path == "models/polarity_classifier.onnx"
    assert config.ml.manifest_path == "models/polarity_classifier.json"
    assert config.ml.use_for_new_revisions is True


def test_ml_configuration_round_trips(tmp_path) -> None:
    path = tmp_path / "config.json"
    config = AppConfig(
        ml=MlConfig(
            model_path="D:/vision/model.onnx",
            manifest_path="D:/vision/model.json",
            use_for_new_revisions=False,
        )
    )
    config.save(path)
    restored = AppConfig.load(path)
    assert restored.ml.model_path == "D:/vision/model.onnx"
    assert restored.ml.manifest_path == "D:/vision/model.json"
    assert restored.ml.use_for_new_revisions is False


def test_untouched_stale_ml_controls_do_not_join_plc_settings_save() -> None:
    live = MlConfig(
        model_path="runtime/models/qualified.onnx",
        manifest_path="runtime/models/qualified.json",
        use_for_new_revisions=True,
    )
    stale_settings_page = MlConfig()

    assert ml_configuration_requires_apply(
        live,
        stale_settings_page,
        user_edited=False,
    ) is False


def test_technician_ml_edit_still_requires_package_validation() -> None:
    live = MlConfig()
    requested = MlConfig(
        model_path="D:/vision/new.onnx",
        manifest_path="D:/vision/new.json",
        use_for_new_revisions=True,
    )

    assert ml_configuration_requires_apply(
        live,
        requested,
        user_edited=True,
    ) is True


def test_reverted_ml_edit_does_not_require_package_validation() -> None:
    live = MlConfig()

    assert ml_configuration_requires_apply(
        live,
        MlConfig(),
        user_edited=True,
    ) is False


def test_unknown_ml_configuration_key_does_not_block_startup(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "camera_backend": "simulation",
                "plc_backend": "simulation",
                "ml": {
                    "model_path": "models/test.onnx",
                    "manifest_path": "models/test.json",
                    "use_for_new_revisions": False,
                    "future_setting_from_newer_release": 123,
                },
                "future_app_setting": "ignored",
            }
        ),
        encoding="utf-8",
    )
    loaded = AppConfig.load(path)
    assert loaded.ml.model_path == "models/test.onnx"
    assert loaded.ml.manifest_path == "models/test.json"
    assert loaded.ml.use_for_new_revisions is False


def test_plc_heartbeat_and_bypass_defaults() -> None:
    config = AppConfig.default()
    assert config.plc_heartbeat_ms == 1000
    assert config.tags.heartbeat == "BatteryVision.Heartbeat"
    assert config.tags.bypass == "BatteryVision.Bypass"
    assert config.tags.fail == "BatteryVision.Fail"
    assert config.failure_retention_days == 30
    assert config.failure_retention_max_gb == 5.0


def test_failure_retention_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    AppConfig(
        failure_retention_days=45,
        failure_retention_max_gb=12.5,
    ).save(path)

    restored = AppConfig.load(path)

    assert restored.failure_retention_days == 45
    assert restored.failure_retention_max_gb == 12.5


def test_plc_heartbeat_and_bypass_round_trip(tmp_path: Path) -> None:
    from battery_inspector.config import PlcTagMap

    path = tmp_path / "config.json"
    config = AppConfig(
        plc_heartbeat_ms=1750,
        tags=PlcTagMap(
            heartbeat="Station.HMIHeartbeat",
            bypass="Station.Bypass",
        ),
    )
    config.save(path)
    restored = AppConfig.load(path)
    assert restored.plc_heartbeat_ms == 1750
    assert restored.tags.heartbeat == "Station.HMIHeartbeat"
    assert restored.tags.bypass == "Station.Bypass"


def test_legacy_config_without_bypass_tag_gets_safe_default(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "camera_backend": "simulation",
                "plc_backend": "simulation",
                "tags": {"heartbeat": "Legacy.Heartbeat"},
            }
        ),
        encoding="utf-8",
    )
    restored = AppConfig.load(path)
    assert restored.tags.heartbeat == "Legacy.Heartbeat"
    assert restored.tags.bypass == "BatteryVision.Bypass"
    assert restored.plc_heartbeat_ms == 1000


def test_legacy_fail_code_tag_migrates_to_binary_fail_tag(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "camera_backend": "simulation",
                "plc_backend": "simulation",
                "tags": {"fail_code": "Station.Vision.FailCode"},
            }
        ),
        encoding="utf-8",
    )

    restored = AppConfig.load(path)

    assert restored.tags.fail == "Station.Vision.Fail"
