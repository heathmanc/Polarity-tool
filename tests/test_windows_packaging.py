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
RELEASE_BUILD = WINDOWS / "build-installer.ps1"
# Both build paths install the same dependency set the same way, so a
# dependency-resolution defect in one is a defect in the other.
BUILD_SCRIPTS = (LOCAL_BUILD, RELEASE_BUILD)


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


def test_the_local_build_reports_whether_cuda_was_bundled() -> None:
    """The build environment decides, not the machine that runs the result.

    `pip install -r requirements.txt` resolves torch from PyPI, which serves the
    CPU-only wheel on Windows, so a workstation with a working CUDA setup can
    still produce a CPU-only application and report no GPU afterwards.
    """

    script = LOCAL_BUILD.read_text(encoding="utf-8")

    assert "torch.cuda.is_available()" in script
    assert "cuda_available" in script, "the build manifest must record it"
    assert "-TorchIndexUrl https://download.pytorch.org/whl/" in script, (
        "the warning must show how to bundle a CUDA build"
    )


def test_the_bundled_torch_probe_is_valid_python() -> None:
    """The probe is embedded in the script, so it can break without notice."""

    import ast

    for script_path in BUILD_SCRIPTS:
        script = script_path.read_text(encoding="utf-8")
        match = re.search(r"@'\n(.*?)\n'@ \| Set-Content", script, re.S)
        assert match is not None, f"{script_path.name}: torch probe here-string missing"

        ast.parse(match.group(1))


def test_python_source_is_never_passed_inline_to_the_interpreter() -> None:
    """PowerShell strips embedded double quotes from native-command arguments.

    Python source passed after -c therefore arrives as a syntax error, which is
    how the torch probe first shipped. build-installer.ps1 sidesteps it by
    writing a probe with no quote characters at all; build-local.ps1 writes its
    probe to a file and passes the path, which has none to strip. Either is
    fine. Quoted source after -c is not.
    """

    for script_path in sorted((ROOT / "packaging" / "windows").glob("*.ps1")):
        script = script_path.read_text(encoding="utf-8")
        for line in script.splitlines():
            if '"-c"' not in line:
                continue
            # The argument list may name a variable; resolve simple assignments.
            for match in re.finditer(r"\$(\w*ProbeCode)\b", line):
                name = match.group(1)
                assignment = re.search(
                    rf"\${name} = (['\"])(.*?)\1", script, re.S
                )
                assert assignment is not None, f"{script_path.name}: {name} not found"
                source = assignment.group(2)
                assert '"' not in source, (
                    f"{script_path.name}: {name} passes double-quoted Python after -c; "
                    "PowerShell will strip those quotes and the interpreter will "
                    "see a syntax error"
                )


def test_every_deletion_in_the_local_build_retries_and_explains_itself() -> None:
    """A stale build output is routinely locked, and the build is expensive.

    Windows will not delete an executable image a live process has mapped, so a
    previous build left running makes the collect step fail with a bare access
    denied error after the freeze has already succeeded. Deletions therefore go
    through Remove-Tree, which retries the momentary holds and names the process
    behind a permanent one.
    """

    script = LOCAL_BUILD.read_text(encoding="utf-8")
    assert "function Remove-Tree {" in script
    assert "function Get-HoldingProcesses {" in script

    body = script.split("function Remove-Tree {", 1)[1]
    outside = script.split("function Remove-Tree {", 1)[0] + body.split("\n}\n", 1)[1]
    stray = [line.strip() for line in outside.splitlines() if "Remove-Item" in line]
    assert not stray, f"deletions must go through Remove-Tree: {stray}"


def test_the_local_build_sleeps_in_units_windows_powershell_understands() -> None:
    """Windows PowerShell 5.1 types Start-Sleep -Seconds as an integer.

    The .cmd wrapper runs the script under 5.1, where a fractional -Seconds
    value rounds to zero and the retry loop degenerates into no waiting at all.
    """

    for script_path in sorted((ROOT / "packaging" / "windows").glob("*.ps1")):
        script = script_path.read_text(encoding="utf-8")
        for line in script.splitlines():
            match = re.search(r"Start-Sleep\s+-Seconds\s+([\d.]+)", line)
            if match is not None:
                assert "." not in match.group(1), (
                    f"{script_path.name}: fractional -Seconds rounds to zero under "
                    "Windows PowerShell 5.1; use -Milliseconds"
                )


def test_a_requested_cuda_torch_survives_the_rest_of_the_install() -> None:
    """Installing the CUDA wheel first does not, on its own, keep it.

    The requirements name torch directly, and pip takes a directly named
    requirement to the newest version its index offers whenever --upgrade is
    passed -- the installed version already satisfying the range does not stop
    it. PyPI's newest Windows wheel is CPU-only and satisfies torch>=2.2, so a
    v0.23.4 build resolved cu128 first and then silently replaced it, shipping a
    CPU-only bundle to a workstation with a 5090 in it.
    """

    for script_path in BUILD_SCRIPTS:
        script = script_path.read_text(encoding="utf-8")

        assert "--constraint" in script, f"{script_path.name}: torch is not pinned"
        assert "torch-constraints.txt" in script

        installs = [
            line
            for line in script.splitlines()
            if "pip" in line and '"install"' in line and "requirements" in line
        ]
        assert installs, f"{script_path.name}: no requirements install found"
        for line in installs:
            assert "$PinnedTorchArguments" in line, (
                f"{script_path.name}: this install can replace the CUDA wheel: "
                f"{line.strip()}"
            )


def test_the_local_build_refuses_to_ship_a_cpu_bundle_that_asked_for_cuda() -> None:
    """A silently CPU-only bundle on a GPU station is the failure to prevent.

    The check also has to separate the two questions it used to conflate: what
    kind of wheel was bundled, which is a property of the build, and whether a
    GPU is visible here, which is a property of the bench.
    """

    for script_path in BUILD_SCRIPTS:
        script = script_path.read_text(encoding="utf-8")

        assert "if ($TorchInfo.cuda_version) {" in script, (
            f"{script_path.name}: the CUDA build must be detected by "
            "torch.version.cuda, not by whether this machine has a driver"
        )
        assert "elseif ($TorchIndexUrl)" in script, script_path.name
        assert "but the installed build is CPU-only" in script, script_path.name


def test_the_installer_compresses_its_payload_in_parallel() -> None:
    """A single-threaded solid stream over this payload takes hours.

    The installer carries the frozen station and the CUDA training runtime --
    several gigabytes -- and Inno Setup's LZMA2 uses one thread unless told
    otherwise, printing nothing while it works. That looked like a hang.
    """

    installer = (WINDOWS / "PolePosition.iss").read_text(encoding="utf-8")

    match = re.search(r"^LZMANumBlockThreads=(\d+)", installer, re.M)
    assert match is not None, "LZMA2 would compress the payload single-threaded"
    assert int(match.group(1)) > 1
