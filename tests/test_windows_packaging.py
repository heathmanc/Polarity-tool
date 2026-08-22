from __future__ import annotations

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
