<#
.SYNOPSIS
    Build a fully bundled Pole Position Windows application locally.

.DESCRIPTION
    Produces a self-contained PolePosition folder containing Python, Qt, OpenCV,
    ONNX Runtime, the pypylon and pycomm3 bindings, and the complete PyTorch /
    Ultralytics training runtime -- everything the application imports.

    This is the sibling of build-installer.ps1, which produces the distributable
    offline installer. That script requires two things this one does not:

      * the licensed Basler pylon Runtime Redistributable, which it embeds so a
        station can install the camera driver offline, and
      * Inno Setup 6.

    Neither is needed to produce a runnable, fully bundled application, so this
    script builds without them. If you have both, prefer build-installer.ps1 --
    it is the change-controlled release path and this script is not a substitute
    for it.

    What cannot be bundled: the Basler pylon Runtime Redistributable is a signed
    vendor driver package, not a Python dependency. The pypylon *bindings* are
    bundled here, but a station that talks to a real camera still needs Basler's
    runtime installed. Production model weights are deliberately excluded too,
    and the build fails if any appear.

.PARAMETER PythonCommand
    Interpreter used to create the build environment. Default "python".

.PARAMETER TorchIndexUrl
    Install torch/torchvision from this index first, for a CUDA build. Example:
    https://download.pytorch.org/whl/cu128

.PARAMETER RequirementsLock
    Install from a pinned requirements file instead of the project's ranges.
    Use a lock file from a previous build to reproduce it exactly.

.PARAMETER OutputDirectory
    Where the finished application is placed. Default: dist\windows-local.

.PARAMETER AllowUnqualifiedPython
    Permit a Python other than the qualified 3.11 x64 baseline. The build is
    then marked unqualified in its manifest and must not be handed to a station.

.PARAMETER SkipChecks
    Skip the source install check and the three graded vision regressions.

.PARAMETER RunTests
    Also run the full pytest suite before building.

.PARAMETER SkipSelfCheck
    Skip running the frozen executable's --verify-install self-check. That check
    imports pypylon, which needs Basler's runtime present on this machine; skip
    it when building on a workstation that has no camera driver installed.

.PARAMETER Archive
    Also produce a .zip of the finished application. Off by default: the folder
    is directly runnable, and compressing a multi-gigabyte bundle takes a long
    time that a local build should not pay for every run.

.PARAMETER Clean
    Delete the build tree before starting.

.EXAMPLE
    .\build-local.ps1

.EXAMPLE
    .\build-local.ps1 -TorchIndexUrl https://download.pytorch.org/whl/cu128 -Clean
#>
[CmdletBinding()]
param(
    [string]$PythonCommand = "python",
    [string]$TorchIndexUrl = "",
    [string]$RequirementsLock = "",
    [string]$OutputDirectory = "",
    [switch]$AllowUnqualifiedPython,
    [switch]$SkipChecks,
    [switch]$RunTests,
    [switch]$SkipSelfCheck,
    [switch]$Archive,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Get-HoldingProcesses {
    param([Parameter(Mandatory = $true)][string]$Path)

    # Windows refuses to delete an executable image that a live process still
    # has mapped, so the previous build left running is the usual culprit.
    $Prefix = [System.IO.Path]::GetFullPath($Path).TrimEnd([char]92) + [char]92
    $Holding = @()
    foreach ($Process in Get-Process) {
        $ProcessPath = ""
        try {
            $ProcessPath = $Process.Path
        } catch {
            continue
        }
        if ($ProcessPath -and $ProcessPath.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) {
            $Holding += "$($Process.ProcessName) (PID $($Process.Id))"
        }
    }
    return $Holding
}

function Remove-Tree {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    # Two different failures land here. A momentary hold by antivirus, the
    # search indexer, or a backup agent clears on its own within a second or
    # two, so retry rather than abandoning a build that is otherwise finished.
    # A mapped image or a read-only attribute does not clear, so report what is
    # actually holding the path instead of the bare access-denied error.
    #
    # Milliseconds, not seconds: Windows PowerShell 5.1 types -Seconds as an
    # integer and would round a fractional delay away to no delay at all.
    $Delays = @(250, 500, 1000, 2000, 4000)
    $LastError = $null
    for ($Attempt = 0; $Attempt -le $Delays.Count; $Attempt++) {
        try {
            Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReadOnly } |
                ForEach-Object {
                    $_.Attributes = [System.IO.FileAttributes](
                        $_.Attributes -band (-bnot [int][System.IO.FileAttributes]::ReadOnly))
                }
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            $LastError = $_
            if ($Attempt -lt $Delays.Count) {
                Start-Sleep -Milliseconds $Delays[$Attempt]
            }
        }
    }

    $Holding = @(Get-HoldingProcesses $Path)
    if ($Holding.Count -gt 0) {
        $Detail = "Still running from that directory: $($Holding -join ', '). Close it and run this script again."
    } else {
        $Detail = "Nothing is running from that directory, so an antivirus scan, an open Explorer window, or a backup agent is holding a file open. Close them, or exclude the build tree from real-time scanning, and run this script again."
    }
    throw "$Description could not be removed: $Path. $Detail Underlying error: $($LastError.Exception.Message)"
}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

if ($env:OS -ne "Windows_NT") {
    throw "Pole Position Windows builds must be produced on 64-bit Windows. PyInstaller is not a cross-compiler."
}

$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDirectory "..\..")).Path
$BuildRoot = Join-Path $ProjectRoot "build\windows-local"
$VenvRoot = Join-Path $BuildRoot ".venv"
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$SpecFile = Join-Path $ScriptDirectory "PolePosition.spec"

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $ProjectRoot "dist\windows-local"
}

$InitText = Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot "battery_inspector\__init__.py")
if ($InitText -notmatch '__version__\s*=\s*"([^"]+)"') {
    throw "Could not determine the Pole Position application version."
}
$Version = $Matches[1]

Write-Step "Pole Position v$Version - local Windows build"

# --- interpreter -----------------------------------------------------------

# A path that does not exist reaches `& $PythonCommand` as a command name, and
# PowerShell reports it as an unrecognised cmdlet -- which reads as though the
# script were broken rather than as a missing interpreter. Say what is actually
# wrong, and say it before anything else has run.
if ($PythonCommand -match "[\\/]") {
    if (-not (Test-Path -LiteralPath $PythonCommand -PathType Leaf)) {
        throw "No interpreter at $PythonCommand. Pass -PythonCommand with the path to a Python 3.11 x64 executable, or omit it to use the 'python' on PATH. A virtual environment that has been deleted or moved is the usual cause; this build creates its own environment and only needs a 3.11 interpreter to start from."
    }
} elseif (-not (Get-Command $PythonCommand -ErrorAction SilentlyContinue)) {
    throw "'$PythonCommand' is not on PATH. Pass -PythonCommand with the full path to a Python 3.11 x64 executable."
}

$PythonProbeCode = "import platform,struct,sys;print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor),struct.calcsize(chr(80))*8,platform.machine(),sep=chr(124))"
$PythonProbe = & $PythonCommand @("-c", $PythonProbeCode)
if ($LASTEXITCODE -ne 0) {
    throw "Python could not be started with '$PythonCommand'."
}
$ProbeParts = $PythonProbe.Trim().Split("|")
$PythonVersion = $ProbeParts[0]
$PythonBits = $ProbeParts[1]

if ($PythonBits -ne "64") {
    throw "A 64-bit interpreter is required. Detected: $PythonProbe"
}
$QualifiedPython = ($PythonVersion -eq "3.11")
if (-not $QualifiedPython) {
    if (-not $AllowUnqualifiedPython) {
        throw "Python 3.11 x64 is the qualified station baseline. Detected $PythonVersion. Pass -AllowUnqualifiedPython to build anyway; the result is marked unqualified and must not be handed to a station."
    }
    Write-Warning "Building on unqualified Python $PythonVersion. This build is for local use only."
}
Write-Host "Interpreter: Python $PythonVersion x$PythonBits"

# --- build environment -----------------------------------------------------

if ($Clean -and (Test-Path $BuildRoot)) {
    Write-Step "Removing previous build tree"
    Remove-Tree -Path $BuildRoot -Description "The previous build tree"
}
New-Item -ItemType Directory -Force -Path $BuildRoot, $OutputDirectory | Out-Null

if (-not (Test-Path (Join-Path $VenvRoot "Scripts\python.exe"))) {
    Write-Step "Creating the build virtual environment"
    Invoke-Checked $PythonCommand @("-m", "venv", $VenvRoot) "Virtual-environment creation"
}
$BuildPython = Join-Path $VenvRoot "Scripts\python.exe"

Write-Step "Installing the complete runtime and training stack (this downloads several GB)"
Invoke-Checked $BuildPython @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel") "Build-tool installation"

if ($RequirementsLock) {
    $RequirementsLock = (Resolve-Path -LiteralPath $RequirementsLock).Path
    $LockedInstall = @("-m", "pip", "install", "--upgrade", "-r", $RequirementsLock)
    if ($TorchIndexUrl) {
        $LockedInstall += @("--extra-index-url", $TorchIndexUrl)
    }
    Invoke-Checked $BuildPython $LockedInstall "Locked dependency installation"
} else {
    $PinnedTorchArguments = @()
    if ($TorchIndexUrl) {
        Invoke-Checked $BuildPython @("-m", "pip", "install", "--upgrade", "torch", "torchvision", "--index-url", $TorchIndexUrl) "PyTorch installation"

        # Installing the CUDA wheel first is not enough to keep it. The
        # requirements name torch directly, and pip always takes a directly
        # named requirement to the newest version its index offers when
        # --upgrade is passed -- the range being already satisfied does not
        # stop it. PyPI's newest Windows wheel is CPU-only and satisfies
        # torch>=2.2, so the very next install quietly uninstalled the CUDA
        # build and the bundle shipped without GPU support.
        #
        # Pinning the versions that were just resolved leaves them in place.
        # The CUDA index goes on as an extra index so those local-version
        # wheels stay resolvable; PyPI alone does not carry them.
        $FrozenPackages = & $BuildPython -m pip freeze
        if ($LASTEXITCODE -ne 0) {
            throw "Could not read the installed PyTorch version."
        }
        $TorchPins = @($FrozenPackages | Where-Object { $_ -match '^(torch|torchvision)==' })
        if ($TorchPins.Count -lt 2) {
            throw "PyTorch installation did not produce pinnable torch and torchvision versions: $($FrozenPackages -join ', ')"
        }
        $TorchConstraints = Join-Path $BuildRoot "torch-constraints.txt"
        $TorchPins | Set-Content -LiteralPath $TorchConstraints -Encoding ASCII
        Write-Host "Holding $($TorchPins -join ' and ') against later resolution."
        $PinnedTorchArguments = @("--constraint", $TorchConstraints, "--extra-index-url", $TorchIndexUrl)
    }
    Invoke-Checked $BuildPython (@("-m", "pip", "install", "--upgrade", "-r", (Join-Path $ProjectRoot "requirements.txt")) + $PinnedTorchArguments) "Pole Position dependency installation"
    Invoke-Checked $BuildPython (@("-m", "pip", "install", "--upgrade", "-r", (Join-Path $ScriptDirectory "requirements-build.txt")) + $PinnedTorchArguments) "PyInstaller installation"
}
Invoke-Checked $BuildPython @("-m", "pip", "check") "Dependency check"

# --- what kind of PyTorch got bundled --------------------------------------
#
# Plain `pip install -r requirements.txt` resolves torch from PyPI, which on
# Windows is the CPU-only wheel. A workstation that had CUDA before a build can
# therefore end up with a CPU-only frozen application, because what matters is
# the build environment, not the machine's own installation.
Write-Step "Checking the bundled PyTorch build"
# The probe goes in a file rather than after -c. PowerShell strips embedded
# double quotes when it hands arguments to a native executable, which turns
# Python source passed inline into a syntax error. build-installer.ps1 avoids
# this by writing its probe without any quote characters at all, using chr();
# that is unreadable for a JSON payload, and a file path carries no quotes to
# strip.
$TorchProbeFile = Join-Path $BuildRoot "torch_probe.py"
@'
import json
import torch

print(
    json.dumps(
        {
            "torch": torch.__version__,
            "cuda": bool(torch.cuda.is_available()),
            "cuda_version": str(getattr(torch.version, "cuda", "") or ""),
            "device": (
                torch.cuda.get_device_name(0) if torch.cuda.is_available() else ""
            ),
        }
    )
)
'@ | Set-Content -LiteralPath $TorchProbeFile -Encoding UTF8

$TorchProbeRaw = & $BuildPython @($TorchProbeFile)
if ($LASTEXITCODE -ne 0) {
    throw "The bundled PyTorch could not be imported."
}
$TorchInfo = $TorchProbeRaw | ConvertFrom-Json
Write-Host "  torch            : $($TorchInfo.torch)"

# Whether the wheel is a CUDA build and whether this bench can see a GPU are
# separate questions, and only the first one is a property of the bundle. A
# build machine with no NVIDIA driver still produces a perfectly good CUDA
# bundle for a station that has one.
if ($TorchInfo.cuda_version) {
    if ($TorchInfo.cuda) {
        Write-Host "  CUDA             : available (CUDA $($TorchInfo.cuda_version), $($TorchInfo.device))" -ForegroundColor Green
    } else {
        Write-Host "  CUDA             : bundled (CUDA $($TorchInfo.cuda_version)), no GPU visible on this build machine" -ForegroundColor Yellow
    }
} elseif ($TorchIndexUrl) {
    # Asked for CUDA and did not get it. Shipping a silently CPU-only bundle to
    # a GPU workstation is the failure this check exists to prevent.
    throw "A CUDA PyTorch index was requested ($TorchIndexUrl) but the installed build is CPU-only ($($TorchInfo.torch)). Something later in the install replaced the CUDA wheel. Rerun with -Clean; if it recurs, check whether the index carries a wheel matching the versions the requirements allow."
} else {
    Write-Warning @"
This build has CPU-only PyTorch. Model training will work but will be far
slower, and the application will report no GPU even on a machine that has one.

Requirements resolve torch from PyPI, which serves the CPU wheel on Windows.
To bundle a CUDA build, rerun with the index matching this workstation's CUDA
support, for example:

  .\build-local.ps1 -Clean -TorchIndexUrl https://download.pytorch.org/whl/cu128
"@
}


# --- pre-build gates -------------------------------------------------------

if (-not $SkipChecks) {
    Write-Step "Verifying the source installation"
    Invoke-Checked $BuildPython @((Join-Path $ProjectRoot "scripts\verify_install.py")) "Source installation check"

    Write-Step "Running the graded vision regressions"
    foreach ($Regression in @("vision_smoke_test.py", "stamp_rotation_smoke_test.py", "terminal_top_gate_smoke_test.py")) {
        Invoke-Checked $BuildPython @((Join-Path $ProjectRoot "scripts\$Regression")) "Regression $Regression"
    }
}

if ($RunTests) {
    Write-Step "Running the full test suite"
    Invoke-Checked $BuildPython @("-m", "pip", "install", "--upgrade", "-e", "$ProjectRoot[dev]") "Development dependency installation"
    Push-Location $ProjectRoot
    try {
        Invoke-Checked $BuildPython @("-m", "pytest") "Test suite"
    } finally {
        Pop-Location
    }
}

# --- freeze ----------------------------------------------------------------

Write-Step "Freezing the application with PyInstaller"
Push-Location $ProjectRoot
try {
    Invoke-Checked $BuildPython @("-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", $DistRoot, "--workpath", $WorkRoot, $SpecFile) "PyInstaller build"
} finally {
    Pop-Location
}

$AppDirectory = Join-Path $DistRoot "PolePosition"
$AppExecutable = Join-Path $AppDirectory "PolePosition.exe"
if (-not (Test-Path -LiteralPath $AppExecutable -PathType Leaf)) {
    throw "PyInstaller did not create PolePosition.exe."
}

# ONNX publishes backend conformance fixtures inside its distribution. The spec
# excludes them; this exact-path cleanup defends against a future third-party
# PyInstaller hook adding the test corpus back during Analysis.
foreach ($TestDirectory in @(
        (Join-Path $AppDirectory "_internal\onnx\backend\test"),
        (Join-Path $AppDirectory "_internal\onnx\test"),
        (Join-Path $AppDirectory "_internal\onnxruntime\datasets"))) {
    if (Test-Path -LiteralPath $TestDirectory -PathType Container) {
        Remove-Tree -Path $TestDirectory -Description "The bundled ONNX test corpus"
    }
}

Write-Step "Checking that no model weights were bundled"
$UnexpectedModels = @(Get-ChildItem -Path $AppDirectory -Recurse -File -Include *.onnx,*.pt,*.pth)
if ($UnexpectedModels.Count -gt 0) {
    $ModelPreview = @($UnexpectedModels | Select-Object -First 20 | ForEach-Object { $_.FullName })
    $AdditionalCount = [Math]::Max(0, $UnexpectedModels.Count - $ModelPreview.Count)
    $AdditionalText = if ($AdditionalCount) { " (and $AdditionalCount more)" } else { "" }
    throw "The frozen application unexpectedly contains model weights: $($ModelPreview -join ', ')$AdditionalText"
}
Write-Host "No model weights present, as required."

# --- provenance ------------------------------------------------------------

$PackagesFile = Join-Path $AppDirectory "THIRD_PARTY_PACKAGES.txt"
$ResolvedPackages = & $BuildPython -m pip freeze
if ($LASTEXITCODE -ne 0) {
    throw "Dependency inventory generation failed with exit code $LASTEXITCODE."
}
$ResolvedPackages | Set-Content -LiteralPath $PackagesFile -Encoding UTF8

$GitCommit = "unknown"
try {
    $CandidateCommit = (& git -C $ProjectRoot rev-parse HEAD 2>$null)
    if ($CandidateCommit) {
        $GitCommit = ([string]$CandidateCommit).Trim()
    }
} catch {
    $GitCommit = "unknown"
}

$Manifest = [ordered]@{
    application = "Pole Position"
    version = $Version
    architecture = "windows-x64"
    build_type = "local-application"
    python = $PythonProbe.Trim()
    qualified_python_baseline = $QualifiedPython
    git_commit = $GitCommit
    built_at_utc = [DateTime]::UtcNow.ToString("o")
    model_weights_included = $false
    full_training_runtime_included = $true
    torch_version = $TorchInfo.torch
    cuda_available = [bool]$TorchInfo.cuda
    cuda_version = $TorchInfo.cuda_version
    pylon_runtime_included = $false
    installer_built = $false
    notes = "Built by build-local.ps1. Fully bundled application without the Basler pylon Runtime Redistributable and without an Inno Setup installer. Use build-installer.ps1 for the distributable release."
}
$Manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $AppDirectory "BUILD-MANIFEST.json") -Encoding UTF8

# --- self-check ------------------------------------------------------------

if ($SkipSelfCheck) {
    Write-Warning "Skipping the frozen self-check at your request; the build has not been exercised."
} else {
    Write-Step "Running the frozen application self-check"
    $VerificationHome = Join-Path $BuildRoot "verification-station"
    New-Item -ItemType Directory -Force -Path $VerificationHome | Out-Null
    $PreviousHome = $env:POLE_POSITION_HOME
    $env:POLE_POSITION_HOME = $VerificationHome
    try {
        $Process = Start-Process -FilePath $AppExecutable -ArgumentList "--verify-install" -Wait -PassThru
        if ($Process.ExitCode -ne 0) {
            $CheckFile = Join-Path $VerificationHome "PolePosition-install-check.json"
            $Detail = if (Test-Path $CheckFile) { Get-Content -Raw $CheckFile } else { "No check report was written." }
            throw "Frozen application self-check failed with exit code $($Process.ExitCode). If it reports that pypylon could not load, this workstation has no Basler pylon runtime installed; rerun with -SkipSelfCheck. Report: $Detail"
        }
    } finally {
        $env:POLE_POSITION_HOME = $PreviousHome
    }
    Write-Host "Self-check passed."
}

# --- deliver ---------------------------------------------------------------

Write-Step "Collecting the finished application"
$TargetName = "Pole-Position-v$Version-win64"
$TargetDirectory = Join-Path $OutputDirectory $TargetName
if (Test-Path -LiteralPath $TargetDirectory) {
    try {
        Remove-Tree -Path $TargetDirectory -Description "The previous build output"
    } catch {
        # The freeze already succeeded, so say where it is. Copying it by hand
        # is cheaper than repeating a PyInstaller run to recover from a lock.
        throw "$($_.Exception.Message) The application built by this run is complete at $AppDirectory and can be copied there by hand once the lock is released."
    }
}
Copy-Item -LiteralPath $AppDirectory -Destination $TargetDirectory -Recurse -Force

$MeasuredSize = (Get-ChildItem -LiteralPath $TargetDirectory -Recurse -File | Measure-Object -Property Length -Sum).Sum
$SizeGb = [Math]::Round($MeasuredSize / 1GB, 2)

$ArchivePath = ""
if ($Archive) {
    Write-Step "Creating the distribution archive (about $SizeGb GB to compress; this takes a while)"
    $ArchivePath = Join-Path $OutputDirectory "$TargetName.zip"
    if (Test-Path -LiteralPath $ArchivePath) {
        Remove-Tree -Path $ArchivePath -Description "The previous distribution archive"
    }
    # ZipFile rather than Compress-Archive: this script runs under Windows
    # PowerShell 5.1 through the .cmd wrapper, where Compress-Archive cannot
    # produce an archive larger than 2 GB. A bundle carrying the training
    # runtime is comfortably past that.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $TargetDirectory,
        $ArchivePath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false)
    $ArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ArchivePath).Hash.ToLowerInvariant()
    "$ArchiveHash *$TargetName.zip" | Set-Content -LiteralPath "$ArchivePath.sha256" -Encoding ASCII
}

Write-Host ""
Write-Host "Pole Position v$Version built successfully." -ForegroundColor Green
Write-Host "  Application : $TargetDirectory  ($SizeGb GB)"
Write-Host "  Executable  : $(Join-Path $TargetDirectory 'PolePosition.exe')"
if ($ArchivePath) {
    Write-Host "  Archive     : $ArchivePath"
    Write-Host "  Checksum    : $ArchivePath.sha256"
}
Write-Host ""
Write-Host "Bundled: Python, Qt/PySide6, OpenCV, ONNX Runtime, pypylon and pycomm3"
Write-Host "bindings, and the complete PyTorch/Ultralytics training runtime."
if ($TorchInfo.cuda) {
    Write-Host "PyTorch $($TorchInfo.torch) with CUDA $($TorchInfo.cuda_version)."
} else {
    Write-Host "PyTorch $($TorchInfo.torch), CPU only - see the warning above." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Still required on a station:" -ForegroundColor Yellow
Write-Host "  * Basler pylon Runtime Redistributable, for real camera hardware."
Write-Host "    It is a signed vendor driver package and cannot be bundled from"
Write-Host "    this repository. build-installer.ps1 embeds it when supplied."
Write-Host "  * The approved production model package (.onnx + .json), which is"
Write-Host "    deliberately never bundled. See models\README.md."
if (-not $QualifiedPython) {
    Write-Host ""
    Write-Warning "Built on unqualified Python $PythonVersion. Do not hand this build to a station."
}
Write-Host ""
Write-Host "For a distributable offline installer, use build-installer.ps1 with the"
Write-Host "pylon Runtime Redistributable and Inno Setup 6. See docs\WINDOWS_INSTALLER.md."
