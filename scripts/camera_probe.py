from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from battery_inspector.config import CameraConfig
from battery_inspector.services.camera import BaslerCameraService, CameraError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Discover Basler cameras through pypylon, automatically open the first "
            "available device, and print its detected capabilities."
        )
    )
    parser.add_argument(
        "--grab",
        action="store_true",
        help="Capture one test frame after opening the camera.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=3000,
        help="Frame timeout used with --grab (default: 3000).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    service = BaslerCameraService(
        CameraConfig(
            selection_mode="first_available",
            timeout_ms=max(250, args.timeout_ms),
            resolution_mode="CameraDefault",
            width=0,
            height=0,
        )
    )

    try:
        devices = service.discover_devices()
        print(f"Detected Basler devices: {len(devices)}")
        for device in devices:
            marker = "AUTO SELECTED" if device.index == 0 else "AVAILABLE"
            print(f"  [{marker}] {device.display_name}")
        if not devices:
            return 2

        if args.grab:
            service.connect()
        state = service.state()
        payload = {
            "runtime": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "pypylon": _package_version("pypylon"),
                "pyside6": _package_version("PySide6"),
            },
            "selection": "first_available",
            "model_or_serial_lock": False,
            "device": state.device.to_dict(),
            "capabilities": state.capabilities.to_dict(),
        }
        if args.grab:
            frame = service.grab()
            payload["test_frame"] = {
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "channels": int(frame.shape[2]) if frame.ndim == 3 else 1,
                "mean_level": float(frame.mean()),
            }
        print(json.dumps(payload, indent=2))
        return 0
    except CameraError as exc:
        print(f"Camera probe failed: {exc}", file=sys.stderr)
        return 3
    finally:
        service.disconnect()


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


if __name__ == "__main__":
    raise SystemExit(main())
