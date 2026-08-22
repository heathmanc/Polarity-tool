from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_module(name: str, required: bool = True) -> bool:
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        state = "REQUIRED" if required else "HARDWARE ONLY"
        print(f"[MISSING] {name:<12} ({state}) — {exc}")
        return not required
    version = getattr(module, "__version__", "installed")
    print(f"[OK]      {name:<12} {version}")
    return True


def main() -> int:
    ok = True
    for module in ("PySide6", "numpy", "cv2"):
        ok &= check_module(module, required=True)
    check_module("pypylon", required=False)
    check_module("pycomm3", required=False)

    if not ok:
        print("\nInstall requirements-demo.txt before launching the HMI.")
        return 2

    import cv2

    from battery_inspector.data import RecipeRepository
    from battery_inspector.services.vision import InspectionPipeline

    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        repository = RecipeRepository(temp_path / "verify.db")
        repository.seed_demo_data()
        recipe = repository.get_active_recipe()
        image = cv2.imread(str(ROOT / "battery_inspector" / "assets" / "demo_battery.jpg"))
        if recipe is None or image is None:
            print("[FAIL] Demo assets or recipe seed are unavailable")
            return 3
        result = InspectionPipeline(output_directory=temp_path).inspect(
            image,
            recipe,
            trigger_source="VERIFY",
        )
        if result.disposition.value != "reject" or result.reason != "POLARITY MARKINGS REVERSED":
            print(
                "[FAIL] Bundled reversed fixture was not rejected correctly: "
                f"{result.disposition.value.upper()} — {result.reason}"
            )
            return 4
        print(
            "[OK]      demo pipeline: "
            f"{result.disposition.value.upper()} — {result.reason}"
        )

    print("\nInstallation verification completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
