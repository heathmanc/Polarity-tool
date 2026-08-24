from __future__ import annotations

import sys
from pathlib import Path

# Load the optional pylon runtime before Qt so the HMI follows the same native
# runtime initialization order as the successful command-line camera probe.
# Hardware remains optional; BaslerCameraService provides the actionable error
# when pypylon or the pylon runtime is unavailable.
try:  # pragma: no cover - depends on the target workstation/runtime
    from pypylon import pylon as _pylon_preload  # type: ignore  # noqa: F401
except Exception:  # noqa: BLE001 - optional native runtime may fail in several ways
    _pylon_preload = None

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from battery_inspector import __version__  # noqa: E402
from battery_inspector.baseline import ensure_clean_v017_baseline  # noqa: E402
from battery_inspector.config import AppConfig  # noqa: E402
from battery_inspector.controller import AppController  # noqa: E402
from battery_inspector.paths import resource_root, station_root  # noqa: E402
from battery_inspector.station_transfer import apply_pending_restore  # noqa: E402
from battery_inspector.ui import MainWindow  # noqa: E402


def project_root() -> Path:
    """Backward-compatible source/resource-root helper."""

    return resource_root()


def load_stylesheet(root: Path) -> str:
    return (root / "battery_inspector" / "ui" / "theme.qss").read_text(encoding="utf-8")


def verify_frozen_install(resources: Path, station: Path) -> int:
    """Run a non-camera installation check used by the Windows installer.

    The result is written to the station directory so corporate deployment
    tooling can retain it without requiring a console window. The separately
    supplied production model is intentionally not part of this check.
    """

    import importlib.metadata
    import json
    from datetime import datetime, timezone

    checks: dict[str, object] = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "resources": str(resources),
        "station_root": str(station),
        "model_required_for_install_check": False,
    }
    issues: list[str] = []
    for relative in (
        Path("battery_inspector/ui/theme.qss"),
        Path("battery_inspector/assets/app_icon.png"),
        Path("battery_inspector/assets/app_icon.ico"),
    ):
        available = (resources / relative).is_file()
        checks[f"resource:{relative.as_posix()}"] = available
        if not available:
            issues.append(f"Missing bundled resource: {relative.as_posix()}")

    for distribution in (
        "PySide6",
        "numpy",
        "opencv-python-headless",
        "onnxruntime",
        "pypylon",
        "pycomm3",
        "torch",
        "torchvision",
        "ultralytics",
        "onnx",
    ):
        try:
            checks[f"package:{distribution}"] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            issues.append(f"Missing packaged dependency: {distribution}")

    try:
        import onnxruntime as ort  # type: ignore

        providers = list(ort.get_available_providers())
        checks["onnxruntime_providers"] = providers
        if "CPUExecutionProvider" not in providers:
            issues.append("ONNX Runtime CPUExecutionProvider is unavailable")
    except Exception as exc:  # noqa: BLE001 - native loader failures are diagnostic output
        issues.append(f"ONNX Runtime failed to load: {exc}")

    try:
        from pypylon import pylon  # type: ignore  # noqa: F401

        checks["pypylon_import"] = True
    except Exception as exc:  # noqa: BLE001 - native loader failures are diagnostic output
        issues.append(f"pypylon failed to load: {exc}")

    try:
        from pycomm3 import LogixDriver  # type: ignore  # noqa: F401

        checks["pycomm3_import"] = True
    except Exception as exc:  # noqa: BLE001
        issues.append(f"pycomm3 failed to load: {exc}")

    try:
        from battery_inspector.ml_training import training_environment

        training = training_environment()
        checks["training_runtime"] = training
        if not bool(training.get("ready")):
            issues.extend(
                f"Training runtime: {item}"
                for item in list(training.get("issues") or [])
            )
    except Exception as exc:  # noqa: BLE001
        issues.append(f"Training runtime failed to load: {exc}")

    probe = station / ".installer-write-test"
    try:
        probe.write_text("Pole Position", encoding="utf-8")
        probe.unlink()
        checks["station_directory_writable"] = True
    except OSError as exc:
        checks["station_directory_writable"] = False
        issues.append(f"Station directory is not writable: {exc}")

    checks["ok"] = not issues
    checks["issues"] = issues
    result_path = station / "PolePosition-install-check.json"
    try:
        result_path.write_text(json.dumps(checks, indent=2), encoding="utf-8")
    except OSError:
        return 2
    return 0 if not issues else 1


def main() -> int:
    resources = resource_root()
    station = station_root()
    if "--verify-install" in sys.argv:
        return verify_frozen_install(resources, station)
    config_path = station / "config.json"
    restore_result: dict = {}
    restore_error = ""
    try:
        restore_result = apply_pending_restore(station, config_path)
    except Exception as exc:  # noqa: BLE001 - keep the current station available after any restore failure
        restore_error = str(exc)
    config = AppConfig.load(config_path)
    config, _baseline = ensure_clean_v017_baseline(station, config, config_path=config_path)
    if _baseline.get("reset_performed"):
        print(f"Pole Position v0.17 clean baseline: archived bench runtime to {_baseline.get('archive', '')}")

    app = QApplication(sys.argv)
    app.setApplicationName("Pole Position")
    app.setApplicationDisplayName("Pole Position")
    app.setOrganizationName("Pole Position")
    app.setWindowIcon(QIcon(str(resources / "battery_inspector" / "assets" / "app_icon.png")))
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(load_stylesheet(resources))

    controller = AppController(station, config, resource_root=resources)
    window = MainWindow(controller)
    if config.fullscreen:
        window.showFullScreen()
    else:
        window.show()
    if restore_result:
        rollback = str(restore_result.get("rollback_backup", ""))
        source_version = str(restore_result.get("source_application_version", "") or "unknown")
        # A restore replaces every recipe with the version in the backup. If that
        # backup predates a gate an operator later switched on -- a red-ring
        # check, a terminal finish -- the station comes back grading without it,
        # and nothing else on screen would say so.
        QTimer.singleShot(
            0,
            lambda: QMessageBox.information(
                window,
                "Workstation restore complete",
                "The imported workstation backup was restored successfully. "
                "Pole Position rebased stored file paths for this PC and preserved a rollback ZIP.\n\n"
                f"Backup written by Pole Position {source_version}; this station runs "
                f"{__version__}.\n\n"
                "Recipes now match the backup, not what was on this station before. "
                "Check each active recipe's terminal settings -- expected marking, "
                "red-ring requirement, and terminal finish -- before returning to "
                "production.\n\n"
                f"Rollback backup: {rollback}",
            ),
        )
    elif restore_error:
        QTimer.singleShot(
            0,
            lambda: QMessageBox.critical(
                window,
                "Workstation restore not applied",
                "Pole Position kept the current workstation data unchanged. "
                "Correct the restore problem and restart again.\n\n"
                f"{restore_error}",
            ),
        )
    controller.defer_initialize()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
