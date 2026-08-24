from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from battery_inspector import __version__


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "packaging" / "windows"


def test_release_versions_match_windows_installer() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    installer = (WINDOWS / "PolePosition.iss").read_text(encoding="utf-8")
    version_info = (WINDOWS / "file_version_info.txt").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in pyproject
    assert f'#define AppVersion "{__version__}"' in installer
    assert f"StringStruct('ProductVersion', '{__version__}')" in version_info


def test_frozen_build_collects_complete_station_and_training_runtime() -> None:
    spec = (WINDOWS / "PolePosition.spec").read_text(encoding="utf-8")
    for package in (
        "onnxruntime",
        "pypylon",
        "pycomm3",
        "torch",
        "torchvision",
        "ultralytics",
        "onnx",
    ):
        assert f'"{package}"' in spec
    assert "app_icon.ico" in spec
    assert "theme.qss" in spec
    assert '"/onnx/backend/test/"' in spec
    assert '"/onnx/test/"' in spec
    assert '"/onnxruntime/datasets/"' in spec
    assert '"onnx.backend.test"' in spec
    assert '"onnx.test"' in spec
    assert '"onnxruntime.datasets"' in spec


def test_installer_preserves_station_data_and_installs_usb_runtime() -> None:
    installer = (WINDOWS / "PolePosition.iss").read_text(encoding="utf-8")
    assert "{commonappdata}\\Pole Position" in installer
    assert "uninsneveruninstall" in installer
    assert "USB_Runtime" in installer
    assert "USB_Camera_Driver" in installer
    assert "--verify-install" in installer
    assert "{autopf64}\\Pole Position" in installer
    assert ".clean_baseline_v017.json" in installer
    assert "{#FrozenAppDirectory}\\*" in installer
    assert "{#ReleaseOutputDirectory}" in installer
    assert "{#InstallerAssetDirectory}\\MODEL_INSTALLATION.txt" in installer
    assert "{#AppIconFile}" in installer
    assert '#define BuildRoot "..\\..\\build' not in installer


def test_build_rejects_accidental_model_weights() -> None:
    script = (WINDOWS / "build-installer.ps1").read_text(encoding="utf-8")
    assert re.search(r"-Include\s+\*\.onnx,\*\.pt,\*\.pth", script)
    assert "full_training_runtime_included = $true" in script
    assert "model_weights_included = $false" in script
    assert "Get-AuthenticodeSignature" in script
    assert 'PylonSigner -notmatch "Basler"' in script
    assert '"Programs\\Inno Setup 6\\ISCC.exe"' in script
    assert "$env:LOCALAPPDATA" in script
    assert '"_internal\\onnx\\backend\\test"' in script
    assert '"_internal\\onnx\\test"' in script
    assert '"_internal\\onnxruntime\\datasets"' in script
    assert "Remove-Item -LiteralPath $TestDirectory -Recurse -Force" in script
    assert "Select-Object -First 20" in script
    assert '"/DFrozenAppDirectory=$AppDirectory"' in script
    assert '"/DReleaseOutputDirectory=$ReleaseRoot"' in script
    assert '"/DInstallerAssetDirectory=$InstallerAssetDirectory"' in script
    assert '"/DAppIconFile=$AppIconFile"' in script
    assert "Push-Location $ScriptDirectory" in script
    wrapper = (ROOT / "BUILD_WINDOWS_INSTALLER.cmd").read_text(encoding="utf-8")
    assert "build-installer.ps1" in wrapper
    assert "-PylonRuntime" in wrapper


def test_python_probe_supports_python_and_full_interpreter_paths() -> None:
    script = (WINDOWS / "build-installer.ps1").read_text(encoding="utf-8")
    assert '[string]$PythonCommand = "python"' in script
    assert '$PythonCommand @("-c", $PythonProbeCode)' in script
    assert "chr(46)" in script
    assert "chr(80)" in script
    assert "chr(124)" in script
    assert 'print(f"' not in script
    match = re.search(r'^\$PythonProbeCode = "([^"]+)"$', script, re.MULTILINE)
    assert match is not None
    result = subprocess.run(
        [sys.executable, "-c", match.group(1)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert re.fullmatch(r"\d+\.\d+\|(?:32|64)\|[^\r\n]+\n?", result.stdout)


def test_model_readme_names_both_separate_model_contracts() -> None:
    text = (WINDOWS / "installer-assets" / "MODEL_INSTALLATION.txt").read_text(
        encoding="utf-8"
    )
    assert "polarity_classifier.onnx" in text
    assert "polarity_classifier.json" in text
    assert "training\\yolo11n-cls.pt" in text


# --- referenced build inputs actually exist --------------------------------
#
# The assertions above match text in the packaging files. That catches a
# deliberate edit but not a rename or move elsewhere in the tree: the spec can
# keep saying "theme.qss" long after the file has moved, and the failure then
# surfaces during a release build rather than in CI. These tests resolve the
# paths the build really consumes.


def _spec_path_parts(node: ast.expr) -> tuple[str, ...] | None:
    """Reconstruct a ``str(ROOT / "a" / "b")`` expression into its parts."""

    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "str":
        return _spec_path_parts(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _spec_path_parts(node.left)
        right = _spec_path_parts(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Name) and node.id == "ROOT":
        return ()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    return None


def _spec_data_sources() -> list[tuple[str, ...]]:
    tree = ast.parse((WINDOWS / "PolePosition.spec").read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and getattr(
            statement.targets[0], "id", ""
        ) == "datas":
            assert isinstance(statement.value, ast.List)
            sources = [_spec_path_parts(item.elts[0]) for item in statement.value.elts]
            assert all(source is not None for source in sources), (
                "A datas entry no longer matches the ROOT / ... form this test parses; "
                "update _spec_path_parts alongside the spec."
            )
            return sources
    raise AssertionError("PolePosition.spec no longer assigns a datas list")


def test_spec_is_valid_python() -> None:
    ast.parse((WINDOWS / "PolePosition.spec").read_text(encoding="utf-8"))


def test_every_bundled_data_source_in_the_spec_exists() -> None:
    sources = _spec_data_sources()

    assert sources, "The frozen build declares no bundled data"
    for parts in sources:
        assert (ROOT.joinpath(*parts)).exists(), f"Spec bundles a missing path: {'/'.join(parts)}"


def test_the_spec_bundles_the_resources_the_install_check_requires() -> None:
    """main.py --verify-install fails the installation without these."""

    bundled = {"/".join(parts) for parts in _spec_data_sources()}

    assert "battery_inspector/ui/theme.qss" in bundled
    assert "battery_inspector/assets" in bundled
    for required in ("app_icon.png", "app_icon.ico"):
        assert (ROOT / "battery_inspector" / "assets" / required).is_file()


def test_installer_asset_inputs_exist() -> None:
    assets = WINDOWS / "installer-assets"

    assert (assets / "MODEL_INSTALLATION.txt").is_file()
    assert (assets / "clean_baseline_v017.json").is_file()
    assert (ROOT / "battery_inspector" / "assets" / "app_icon.ico").is_file()


def test_the_build_script_and_installer_are_present_and_non_empty() -> None:
    for name in ("build-installer.ps1", "PolePosition.iss", "PolePosition.spec"):
        assert (WINDOWS / name).stat().st_size > 0
    assert (ROOT / "BUILD_WINDOWS_INSTALLER.cmd").stat().st_size > 0


# --- the local application build -------------------------------------------
#
# build-local.ps1 produces a fully bundled application without the two release
# prerequisites: the licensed Basler pylon Runtime Redistributable and Inno
# Setup. These assertions pin the properties that make it safe to run -- it must
# keep the release build's guards rather than trading them for convenience.

LOCAL_BUILD = WINDOWS / "build-local.ps1"


def test_local_build_script_and_wrapper_exist() -> None:
    assert LOCAL_BUILD.is_file()
    wrapper = (ROOT / "BUILD_WINDOWS_APP.cmd").read_text(encoding="utf-8")
    assert "build-local.ps1" in wrapper
    # Arguments must reach the script, or none of its switches are usable.
    assert "%*" in wrapper


def test_local_build_refuses_to_run_off_windows() -> None:
    script = LOCAL_BUILD.read_text(encoding="utf-8")
    assert '$env:OS -ne "Windows_NT"' in script


def test_local_build_bundles_the_complete_runtime() -> None:
    """"Everything bundled" means the full requirements set and the same spec."""

    script = LOCAL_BUILD.read_text(encoding="utf-8")
    assert '"requirements.txt"' in script
    assert "requirements-build.txt" in script
    assert "PolePosition.spec" in script
    assert '"PyInstaller"' in script


def test_local_build_keeps_the_model_weight_guard() -> None:
    """A local build must not become the one that leaks weights."""

    script = LOCAL_BUILD.read_text(encoding="utf-8")
    assert re.search(r"-Include\s+\*\.onnx,\*\.pt,\*\.pth", script)
    assert "model_weights_included = $false" in script
    assert "pylon_runtime_included = $false" in script


def test_local_build_strips_the_onnx_test_corpora() -> None:
    script = LOCAL_BUILD.read_text(encoding="utf-8")
    assert '"_internal\\onnx\\backend\\test"' in script
    assert '"_internal\\onnx\\test"' in script
    assert '"_internal\\onnxruntime\\datasets"' in script


def test_local_build_defends_the_qualified_python_baseline() -> None:
    script = LOCAL_BUILD.read_text(encoding="utf-8")
    assert "AllowUnqualifiedPython" in script
    assert '$PythonVersion -eq "3.11"' in script
    assert "qualified_python_baseline" in script


def test_local_build_avoids_the_compress_archive_size_limit() -> None:
    """The wrapper runs powershell.exe, where Compress-Archive caps at 2 GB.

    A bundle carrying the training runtime is well past that, so the archive has
    to come from ZipFile instead.
    """

    script = LOCAL_BUILD.read_text(encoding="utf-8")
    assert "System.IO.Compression.ZipFile" in script
    invocations = [
        line
        for line in script.splitlines()
        if line.strip().startswith("Compress-Archive")
    ]
    assert invocations == [], invocations


def test_local_build_does_not_claim_to_replace_the_release_path() -> None:
    script = LOCAL_BUILD.read_text(encoding="utf-8")
    assert "build-installer.ps1" in script
    assert "installer_built = $false" in script


def test_every_script_importing_the_package_bootstraps_sys_path() -> None:
    """`python scripts\\x.py` must work from a plain checkout.

    Scripts are run by path, not as modules, so the repository root is not on
    sys.path unless the script puts it there. Every script that imports
    battery_inspector without doing so fails with ModuleNotFoundError for
    whoever runs it -- which is how clear_pending_restore.py first shipped.
    """

    offenders = []
    for script in sorted((ROOT / "scripts").glob("*.py")):
        source = script.read_text(encoding="utf-8")
        imports_package = re.search(
            r"^\s*(from|import)\s+battery_inspector", source, re.MULTILINE
        )
        if imports_package and "sys.path.insert" not in source:
            offenders.append(script.name)

    assert offenders == [], (
        f"these scripts import battery_inspector without putting the repository "
        f"root on sys.path: {offenders}"
    )
