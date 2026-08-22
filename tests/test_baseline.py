from __future__ import annotations

from pathlib import Path

from battery_inspector.baseline import MARKER_NAME, ensure_clean_v017_baseline
from battery_inspector.config import AppConfig, MlConfig


def test_v017_baseline_archives_bench_runtime_and_preserves_station_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runtime = project / "runtime"
    (runtime / "ml_training" / "samples").mkdir(parents=True)
    (runtime / "ml_training" / "samples" / "old.png").write_bytes(b"old")
    (runtime / "battery_inspector.db").write_bytes(b"db")
    config_path = project / "config.json"
    config = AppConfig(
        camera_backend="auto",
        plc_backend="simulation",
        plc_address="10.20.30.40/1",
        ml=MlConfig(
            model_path=str(runtime / "models" / "legacy.onnx"),
            manifest_path=str(runtime / "models" / "legacy.json"),
            use_for_new_revisions=True,
        ),
    ).normalized()
    config.save(config_path)

    updated, report = ensure_clean_v017_baseline(project, config, config_path=config_path)

    assert report["reset_performed"] is True
    archive = Path(report["archive"])
    assert (archive / "battery_inspector.db").is_file()
    assert (archive / "ml_training" / "samples" / "old.png").is_file()
    assert (runtime / MARKER_NAME).is_file()
    assert not (runtime / "battery_inspector.db").exists()
    assert updated.plc_address == "10.20.30.40/1"
    assert updated.ml.model_path == "models/polarity_classifier.onnx"

    second, second_report = ensure_clean_v017_baseline(project, updated, config_path=config_path)
    assert second_report["reset_performed"] is False
    assert second.plc_address == updated.plc_address
