"""Report the state that decides PASS or REJECT on this station.

Use this when inspections are grading in a way that does not match the parts in
front of the camera -- most urgently when something passes that should not.

It reports the acquisition source, the active recipe and every gate bound to it,
the classifier and the model it is pinned to, and the readiness the inspection
pipeline itself computes. It reads state and grades nothing, so it is safe to
run on a live station, though the station should be out of production while a
false result is being investigated.

    python scripts/diagnose_station.py
    python scripts/diagnose_station.py --station "C:\\ProgramData\\Pole Position"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from battery_inspector.config import AppConfig  # noqa: E402
from battery_inspector.controller import AppController  # noqa: E402
from battery_inspector.evidence import sha256_file  # noqa: E402
from battery_inspector.paths import station_root  # noqa: E402


def flag(ok: bool, good: str = "ok", bad: str = "PROBLEM") -> str:
    return good if ok else bad


def report_acquisition(controller: AppController) -> list[str]:
    """A simulated camera grades a bundled image, not the part on the fixture."""

    problems: list[str] = []
    config = controller.config
    print("ACQUISITION")
    print(f"  Camera backend configured : {config.camera_backend}")
    print(f"  PLC backend configured    : {config.plc_backend}")
    if config.camera_backend == "simulation":
        problems.append(
            "The camera backend is SIMULATION. Inspections grade a bundled demo "
            "image, not the battery on the fixture, so their result says nothing "
            "about the part. Settings / Camera -> select Basler."
        )
    if config.plc_backend == "simulation":
        print("  (PLC simulation is active; no physical interlock is being driven.)")
    print()
    return problems


def report_recipe(controller: AppController) -> list[str]:
    problems: list[str] = []
    recipe = controller.active_recipe
    print("ACTIVE RECIPE")
    if recipe is None:
        print("  none")
        problems.append("No active recipe; the station cannot grade anything.")
        print()
        return problems

    print(f"  Number / name  : {recipe.recipe_number} / {recipe.name}")
    print(f"  Revision       : {recipe.revision}   status: {recipe.status.value}")
    print(
        f"  Validation     : {recipe.validation_runs_passed}"
        f"/{recipe.validation_runs_required} runs, "
        f"{recipe.validation_pass_record_count} evidence records "
        f"-> complete={recipe.validation_complete}"
    )

    reference = recipe.reference_image
    if reference is None or not reference.path:
        problems.append("The recipe has no reference image.")
        print("  Reference      : MISSING")
    else:
        path = Path(reference.path)
        exists = path.is_file()
        line = f"  Reference      : {path}"
        print(line)
        if not exists:
            problems.append(f"The recipe reference image is missing: {path}")
            print(f"                   {flag(False)}: file not found")
        else:
            digest = sha256_file(path)
            matches = not reference.sha256 or digest.lower() == reference.sha256.lower()
            print(f"                   present, sha256 {flag(matches, 'matches', 'DOES NOT MATCH')}")
            if not matches:
                problems.append(
                    "The reference image on disk does not match the digest recorded "
                    "in the recipe. The recipe is grading against a different image "
                    "than the one it was validated with."
                )
        if recipe.reference_is_demo:
            problems.append(
                "The recipe reference is the bundled demonstration image, not a "
                "capture of the real part."
            )

    print(f"  Terminals      : {len(recipe.terminals)}")
    if not recipe.terminals:
        problems.append(
            "The recipe defines no terminals. There is nothing to check, which is "
            "the most direct way for every part to pass."
        )
    for terminal in recipe.terminals:
        print(
            f"    - {terminal.key:<10} role={terminal.role.value:<8} "
            f"expects={terminal.expected_marking.value:<8} "
            f"red_ring_required={terminal.red_ring_required}"
        )
    expected = {terminal.expected_marking.value for terminal in recipe.terminals}
    if recipe.terminals and len(expected) == 1:
        problems.append(
            f"Every terminal expects the same marking ({expected.pop()}). A reversed "
            "battery cannot be distinguished from a correct one."
        )
    print()
    return problems


def report_classifier(controller: AppController) -> list[str]:
    problems: list[str] = []
    recipe = controller.active_recipe
    print("POLARITY CLASSIFIER")
    if recipe is None:
        print("  no active recipe")
        print()
        return problems

    settings = recipe.classifier_settings.normalized()
    print(f"  Method            : {settings.method}")
    print(f"  Minimum confidence: {settings.minimum_confidence:.0%}")
    if settings.method == "onnx_ml":
        print(f"  Bound model       : {settings.ml_model_id} {settings.ml_model_version}")
        print(f"  Bound sha256      : {settings.ml_model_sha256[:16] or '(none)'}")

    info = controller.ml_model_info()
    installed = str(info.get("model_path", "") or "")
    print(f"  Installed model   : {installed or '(none)'}")
    if installed:
        path = Path(installed)
        if not path.is_file():
            # Only a fault when this recipe actually grades with the model. A
            # reference_template recipe does not consult it at all.
            if settings.method == "onnx_ml":
                problems.append(f"The installed model file is missing: {path}")
                print(f"                      {flag(False)}: file not found")
            else:
                print("                      not present (this recipe does not use ML)")
        elif settings.method == "onnx_ml" and settings.ml_model_sha256:
            digest = sha256_file(path)
            matches = digest.lower() == settings.ml_model_sha256.lower()
            print(f"                      sha256 {flag(matches, 'matches the recipe binding', 'DOES NOT MATCH the recipe binding')}")
            if not matches:
                problems.append(
                    "The installed model is not the one this recipe was validated "
                    "against. Revalidate the recipe against the installed model."
                )
    print(f"  Pipeline status   : {controller.pipeline.classifier_status_for_recipe(recipe)}")
    print()
    return problems


def report_readiness(controller: AppController) -> list[str]:
    readiness = controller.inspection_readiness()
    print("PIPELINE READINESS")
    print(f"  Ready            : {readiness['ready']}")
    print(f"  Locator          : {readiness['locator_status']}")
    print(f"  Classifier       : {readiness['classifier_status']}")
    issues = list(readiness["issues"])
    if issues:
        print("  Blocking issues  :")
        for issue in issues:
            print(f"    - {issue}")
    print()
    # A station that is not ready fails closed, so this is not a false-pass cause.
    return []


def report_history(controller: AppController) -> list[str]:
    """Retained rows are non-PASS only, so their absence is itself informative."""

    summary = controller.repository.inspection_summary()
    print("RETAINED INSPECTION HISTORY")
    print(f"  Recorded product results : {summary['part_count']}")
    print(f"  of which rejects         : {summary['fail_count']}")
    print("  (PASS cycles are memory-only by policy and never recorded here,")
    print("   which is also why EXPORT INSPECTION ZIP is unavailable for a PASS.)")
    print()
    return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report what decides PASS or REJECT on this station."
    )
    parser.add_argument(
        "--station",
        type=Path,
        default=None,
        help="Station root. Detected automatically when omitted.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the raw state as JSON.")
    arguments = parser.parse_args()

    root = (arguments.station or station_root(create=False)).expanduser().resolve()
    config_path = root / "config.json"
    if not config_path.is_file():
        print(f"No station configuration at {config_path}")
        print("Pass --station with the station root, for example:")
        print('  python scripts/diagnose_station.py --station "C:\\ProgramData\\Pole Position"')
        return 2

    config = AppConfig.load(config_path)
    controller = AppController(root, config, resource_root=ROOT)
    try:
        if arguments.json:
            recipe = controller.active_recipe
            print(
                json.dumps(
                    {
                        "station_root": str(root),
                        "camera_backend": config.camera_backend,
                        "plc_backend": config.plc_backend,
                        "active_recipe": recipe.to_dict() if recipe else None,
                        "readiness": controller.inspection_readiness(),
                        "ml_model": controller.ml_model_info(),
                    },
                    default=str,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        print(f"Station: {root}")
        print()
        problems: list[str] = []
        problems += report_acquisition(controller)
        problems += report_recipe(controller)
        problems += report_classifier(controller)
        problems += report_readiness(controller)
        problems += report_history(controller)

        if problems:
            print("FINDINGS")
            for item in problems:
                print(f"  * {item}")
            print()
            print("Keep the station out of production until these are resolved.")
            return 1

        print("No decision-affecting problem found in the station's stored state.")
        print("If parts are still grading wrongly, the recipe's regions or expected")
        print("markings may not match the fixture; re-run guided validation.")
        return 0
    finally:
        controller.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
