# Pole Position Windows Installer

## Delivered installation

`build-installer.ps1` creates one offline, x64 Windows installer. A target
workstation does not need Python, pip, Visual Studio, or internet access.

The installer contains:

- Pole Position HMI and Python 3.11 runtime.
- PySide6 and required Qt plugins.
- NumPy and OpenCV.
- ONNX and ONNX Runtime CPU inference.
- pypylon and the official Basler pylon USB3 runtime/driver supplied at build
  time.
- pycomm3 for Allen-Bradley Logix communication.
- PyTorch, torchvision, Ultralytics, and ONNX export support used by guided HMI
  training.
- The Pole Position icon, stylesheet, and demonstration assets.

The installer deliberately contains no `.onnx`, `.pt`, or `.pth` model weight.
The build fails if one is accidentally collected.

## Local application build without the installer

`packaging\windows\build-local.ps1` produces the same fully bundled
application as the release build, but stops short of the Inno Setup installer
and does not need the Basler pylon Runtime Redistributable. Use it to get a
runnable Windows build on a workstation that has neither.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\build-local.ps1 -Clean
```

or from the source root:

```cmd
BUILD_WINDOWS_APP.cmd -Clean
```

The result is `dist\windows-local\Pole-Position-v<version>-win64\`, containing
`PolePosition.exe` alongside Python, Qt, OpenCV, ONNX Runtime, the pypylon and
pycomm3 bindings, and the complete PyTorch/Ultralytics training runtime. Add
`-Archive` for a `.zip` and checksum.

It keeps the release build's guards: the same PyInstaller spec, the ONNX
test-corpus cleanup, the model-weight check, the dependency inventory, a build
manifest, and the frozen `--verify-install` self-check.

Useful switches:

| Switch | Effect |
| --- | --- |
| `-TorchIndexUrl <url>` | Install a CUDA PyTorch build first, as for the release build |
| `-RequirementsLock <file>` | Reproduce an earlier build from its lock file |
| `-RunTests` | Run the full pytest suite before freezing |
| `-SkipChecks` | Skip the source check and the three graded regressions |
| `-SkipSelfCheck` | Skip the frozen self-check on a workstation with no pylon runtime installed |
| `-AllowUnqualifiedPython` | Build on a Python other than the qualified 3.11 x64 |
| `-Archive` | Also produce the `.zip` and its checksum |

**CUDA is not bundled unless you ask for it.** The requirements resolve torch
from PyPI, which serves the CPU-only wheel on Windows, so a build made without
`-TorchIndexUrl` produces an application that reports no GPU even on a machine
that has one — what matters is the build environment, not the workstation. The
build prints which PyTorch it bundled and records `cuda_available` in
`BUILD-MANIFEST.json`. For a CUDA build, pass the index matching the
workstation's CUDA support:

```powershell
.\build-local.ps1 -Clean -TorchIndexUrl https://download.pytorch.org/whl/cu128
```

Two things are still not bundled, and neither can be:

- **The Basler pylon Runtime Redistributable.** The pypylon *bindings* are
  bundled, but a station driving real camera hardware needs Basler's signed
  driver package installed. The release installer embeds it when supplied.
- **Production model weights.** Deliberately separate; see `models/README.md`.
  The build fails if any `.onnx`, `.pt`, or `.pth` appears in the frozen tree.

This build is for local use, commissioning benches, and development. It is not
the change-controlled release artifact: a station receives the installer
produced by `build-installer.ps1`, and a build made on an unqualified Python is
marked as such in its `BUILD-MANIFEST.json`.

## Build-computer prerequisites

Use the local Windows computer that will be the controlled packaging resource.
Install:

1. 64-bit Windows 10 22H2 or Windows 11.
2. 64-bit Python 3.11. Select **Add Python to PATH**, or use the Python launcher
   installed by python.org.
3. Inno Setup 6.
4. The official Basler **pylon Runtime Redistributable** executable matching the
   pylon/pypylon version qualified for the station. Keep the downloaded file;
   the build embeds it into the Pole Position installer.
5. Internet access while building so pip can resolve and download the packages.
   The resulting installer itself is offline.

Do not use Microsoft Store Python, 32-bit Python, Python 3.12/3.13, or a Linux
computer for the release build. PyInstaller must build the Windows executable
on Windows.

## Build command

Open PowerShell in the source root and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\build-installer.ps1 `
  -PylonRuntime "C:\Installers\pylon_Runtime_x64.exe" `
  -Clean
```

For the standard CPU build, the root-level wrapper provides the same operation:

```cmd
BUILD_WINDOWS_INSTALLER.cmd "C:\Installers\pylon_Runtime_x64.exe"
```

The argument can be supplied by dragging the pylon Runtime executable onto the
CMD file in Windows Explorer.

The script defaults to `python`, so an activated Python 3.11 virtual
environment works without an override. You can also pass the full path to a
Python 3.11 executable explicitly:

```powershell
.\packaging\windows\build-installer.ps1 `
  -PylonRuntime "C:\Installers\pylon_Runtime_x64.exe" `
  -PythonCommand "C:\Python311\python.exe" `
  -Clean
```

The default pip PyTorch build is appropriate for CPU-only MS-01/MS-03
workstations. To build for a station with a qualified NVIDIA CUDA environment,
pass the PyTorch index selected for that driver/toolkit combination:

```powershell
.\packaging\windows\build-installer.ps1 `
  -PylonRuntime "C:\Installers\pylon_Runtime_x64.exe" `
  -TorchIndexUrl "https://download.pytorch.org/whl/cu128" `
  -Clean
```

The build pins the CUDA `torch` and `torchvision` versions it resolves and
carries that pin through every later install, because `pip install --upgrade`
takes a directly named requirement to the newest version its index offers even
when the installed one already satisfies the range -- and PyPI's newest Windows
wheel is CPU-only. It then verifies what was actually resolved: passing
`-TorchIndexUrl` and ending up with a CPU-only build fails the release rather
than shipping a station bundle without GPU support. Whether the build machine
itself has a GPU does not affect the result.

Use one controlled dependency/GPU choice for every station in the release.

For a production release, sign both the application and final installer with
the organization's Windows code-signing certificate:

```powershell
.\packaging\windows\build-installer.ps1 `
  -PylonRuntime "C:\Installers\pylon_Runtime_x64.exe" `
  -SignCertificateThumbprint "CERTIFICATE_THUMBPRINT" `
  -Clean
```

The script locates the x64 Windows SDK `signtool.exe` automatically. Use
`-SignTool` when the SDK is installed in a nonstandard location. Unsigned builds
are allowed for bench work but produce an explicit warning and may trigger
Windows SmartScreen or Unknown Publisher prompts.

The first controlled build writes a complete transitive dependency lock beside
the installer. Pass that file back with `-RequirementsLock` to reproduce the
same Python package set for later station builds. Continue passing the same
`-TorchIndexUrl` when the lock contains a CUDA- or CPU-index-specific PyTorch
wheel.

## Build actions and output

The script:

1. Verifies Windows x64 and Python 3.11 x64.
2. Creates an isolated build virtual environment.
3. Installs the complete application and training requirements.
4. Verifies that the supplied pylon redistributable has a valid Basler
   Authenticode signature.
5. Runs pip dependency checks and the existing installation/pipeline check.
6. Freezes Pole Position as a one-directory Windows application.
7. Rejects any accidentally bundled model weights.
8. Records every resolved Python package, pylon signer, and pylon SHA-256.
9. Runs the frozen application self-check from a temporary station directory.
10. Builds the offline Inno Setup installer.
11. Writes the installer SHA-256 sidecar.

Successful output is placed under `dist\windows`:

```text
Pole-Position-v0.23.4-Setup-x64.exe
Pole-Position-v0.23.4-Setup-x64.exe.sha256
Pole-Position-v0.23.4-requirements-lock.txt
```

The intermediate frozen directory and dependency manifest remain under
`build\windows` for troubleshooting.

## Installed layout

Read-only application files:

```text
C:\Program Files\Pole Position\
```

Writable station files:

```text
C:\ProgramData\Pole Position\
    config.json
    models\
    runtime\
    PolePosition-install-check.json
```

All authenticated local Windows users receive modify access to the station
directory. Program Files remains protected. Upgrade installers replace only
application files. They preserve settings, recipes, validation evidence,
training samples, installed models, and retained failures.

The installer stamps a fresh station as already using the current post-v0.17
ML/runtime contract. This prevents a separately copied training checkpoint from
being mistaken for legacy bench data and archived on the first HMI launch. An
existing baseline marker is never overwritten.

Uninstall also preserves the ProgramData station directory so an accidental
uninstall cannot erase production configuration. Use Pole Position's backup
function before intentionally decommissioning a workstation.

## Basler runtime behavior

The Pole Position installer silently installs these official pylon features:

- `Cpp_Runtime`
- `USB_Runtime`
- `USB_Camera_Driver`

It uses Basler's `/allowupgrade` option. The pylon redistributable is a shared
machine component and is not removed when Pole Position is uninstalled.

## Separately supplied model files

The package supports two distinct model types that are not in the installer.

### Qualified production package

Supply both files together:

```text
polarity_classifier.onnx
polarity_classifier.json
```

Use **Settings > Vision / ML** to browse to the pair and choose **Save &
Apply**. Pole Position verifies the manifest, SHA-256, runtime, input crop
contract, and four required classes. Replacing the qualified model does not
automatically requalify recipes; affected recipes require a new revision and
physical validation.

### Offline training checkpoint

Place the separately downloaded classification starting checkpoint at:

```text
C:\ProgramData\Pole Position\runtime\models\training\yolo11n-cls.pt
```

The guided ML Training page defaults to this path. It also allows Engineering
to browse to a separately qualified small or medium classification checkpoint.

## Installation acceptance check

At the end of installation, Pole Position runs `--verify-install` without
opening the HMI. It verifies packaged resources, required distribution
metadata, ONNX Runtime CPU support, pypylon loading, pycomm3 loading, and
ProgramData write access. Results are written to:

```text
C:\ProgramData\Pole Position\PolePosition-install-check.json
```

This check does not require the camera, PLC, or separately supplied model. The
normal commissioning procedure must still verify the physical Basler camera,
PLC tags, training runtime/device, model package, recipe validation, and a real
PASS/FAIL cycle.
