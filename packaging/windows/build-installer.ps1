[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$PylonRuntime,

    [string]$PythonCommand = "python",

    [string]$InnoCompiler = "",

    [string]$TorchIndexUrl = "",

    [string]$RequirementsLock = "",

    [string]$SignCertificateThumbprint = "",

    [string]$SignTool = "",

    [string]$TimestampUrl = "http://timestamp.digicert.com",

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

if ($env:OS -ne "Windows_NT") {
    throw "Pole Position Windows installers must be built on 64-bit Windows."
}

$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDirectory "..\..")).Path
$BuildRoot = Join-Path $ProjectRoot "build\windows"
$VenvRoot = Join-Path $BuildRoot ".venv"
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$ReleaseRoot = Join-Path $ProjectRoot "dist\windows"
$SpecFile = Join-Path $ScriptDirectory "PolePosition.spec"
$InnoScript = Join-Path $ScriptDirectory "PolePosition.iss"
$PylonRuntime = (Resolve-Path -LiteralPath $PylonRuntime).Path
$PylonSignature = Get-AuthenticodeSignature -LiteralPath $PylonRuntime
if ($PylonSignature.Status -ne "Valid") {
    throw "The supplied Basler pylon Runtime does not have a valid Authenticode signature: $($PylonSignature.Status)."
}
$PylonSigner = [string]$PylonSignature.SignerCertificate.Subject
if ($PylonSigner -notmatch "Basler") {
    throw "The supplied pylon Runtime is not signed by Basler. Signer: $PylonSigner"
}

$InitText = Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot "battery_inspector\__init__.py")
if ($InitText -notmatch '__version__\s*=\s*"([^"]+)"') {
    throw "Could not determine the Pole Position application version."
}
$Version = $Matches[1]

$PythonProbeCode = "import platform,struct,sys;print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor),struct.calcsize(chr(80))*8,platform.machine(),sep=chr(124))"
$PythonProbe = & $PythonCommand @("-c", $PythonProbeCode)
if ($LASTEXITCODE -ne 0) {
    throw "Python could not be started with '$PythonCommand'."
}
$ProbeParts = $PythonProbe.Trim().Split("|")
if ($ProbeParts[0] -ne "3.11" -or $ProbeParts[1] -ne "64") {
    throw "Use 64-bit Python 3.11 to build Pole Position. Detected: $PythonProbe"
}

if ($Clean -and (Test-Path $BuildRoot)) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $BuildRoot, $ReleaseRoot | Out-Null

if (-not (Test-Path (Join-Path $VenvRoot "Scripts\python.exe"))) {
    Invoke-Checked $PythonCommand @("-m", "venv", $VenvRoot) "Virtual-environment creation"
}
$BuildPython = Join-Path $VenvRoot "Scripts\python.exe"

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

        # Installing the CUDA wheel first does not keep it. requirements.txt
        # names torch directly, and pip takes a directly named requirement to
        # the newest version its index offers when --upgrade is passed, even
        # though the installed version already satisfies the range. PyPI's
        # newest Windows wheel is CPU-only and satisfies torch>=2.2, so the
        # next install would uninstall the CUDA build and the release would
        # reach a GPU station without GPU support. Pin what was resolved.
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
    Invoke-Checked $BuildPython (@("-m", "pip", "install", "--upgrade", "-r", (Join-Path $ScriptDirectory "requirements-build.txt")) + $PinnedTorchArguments) "Installer build dependency installation"
}
Invoke-Checked $BuildPython @("-m", "pip", "check") "Dependency check"

# What kind of PyTorch is in the build environment decides what the station
# gets, whether or not this build machine has a GPU of its own. The probe goes
# in a file: PowerShell strips embedded double quotes when it hands arguments
# to a native executable, so JSON-emitting source cannot be passed after -c.
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
Write-Host "PyTorch in the release environment: $($TorchInfo.torch)"
if ($TorchInfo.cuda_version) {
    $DeviceNote = if ($TorchInfo.cuda) { $TorchInfo.device } else { "no GPU visible on this build machine" }
    Write-Host "  CUDA $($TorchInfo.cuda_version) ($DeviceNote)" -ForegroundColor Green
} elseif ($TorchIndexUrl) {
    # Asked for CUDA and did not get it. A release that silently drops GPU
    # support reaches every station in the release.
    throw "A CUDA PyTorch index was requested ($TorchIndexUrl) but the installed build is CPU-only ($($TorchInfo.torch)). Rerun with -Clean; if it recurs, check whether the index carries a wheel matching the versions the requirements allow."
} else {
    Write-Host "  CPU-only. Pass -TorchIndexUrl to build for a CUDA station." -ForegroundColor Yellow
}
Invoke-Checked $BuildPython @((Join-Path $ProjectRoot "scripts\verify_install.py")) "Pole Position source installation check"

if (-not $InnoCompiler) {
    $CandidateCompilers = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    $Candidates = @($CandidateCompilers | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) })
    if ($Candidates.Count -gt 0) {
        $InnoCompiler = $Candidates[0]
    }
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
    throw "Inno Setup 6 was not found. Install it or pass -InnoCompiler with the path to ISCC.exe."
}
$InnoCompiler = (Resolve-Path -LiteralPath $InnoCompiler).Path

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

# ONNX publishes backend conformance fixtures inside its Python distribution.
# They are development tests, not runtime resources or Pole Position models.
# The spec excludes them; this exact-path cleanup is a defense against a future
# third-party PyInstaller hook adding the test corpus back during Analysis.
$OnnxTestDirectories = @(
    (Join-Path $AppDirectory "_internal\onnx\backend\test"),
    (Join-Path $AppDirectory "_internal\onnx\test"),
    (Join-Path $AppDirectory "_internal\onnxruntime\datasets")
)
foreach ($TestDirectory in $OnnxTestDirectories) {
    if (Test-Path -LiteralPath $TestDirectory -PathType Container) {
        Remove-Item -LiteralPath $TestDirectory -Recurse -Force
    }
}

$UnexpectedModels = @(Get-ChildItem -Path $AppDirectory -Recurse -File -Include *.onnx,*.pt,*.pth)
if ($UnexpectedModels.Count -gt 0) {
    $ModelPreview = @($UnexpectedModels | Select-Object -First 20 | ForEach-Object { $_.FullName })
    $AdditionalCount = [Math]::Max(0, $UnexpectedModels.Count - $ModelPreview.Count)
    $AdditionalText = if ($AdditionalCount) { " (and $AdditionalCount more)" } else { "" }
    throw "The frozen application unexpectedly contains model weights: $($ModelPreview -join ', ')$AdditionalText"
}

$PackagesFile = Join-Path $AppDirectory "THIRD_PARTY_PACKAGES.txt"
$ResolvedPackages = & $BuildPython -m pip freeze
if ($LASTEXITCODE -ne 0) {
    throw "Dependency inventory generation failed with exit code $LASTEXITCODE."
}
$ResolvedPackages | Set-Content -LiteralPath $PackagesFile -Encoding UTF8
$ReleaseLock = Join-Path $ReleaseRoot "Pole-Position-v$Version-requirements-lock.txt"
Copy-Item -LiteralPath $PackagesFile -Destination $ReleaseLock -Force

if ($SignCertificateThumbprint) {
    if (-not $SignTool) {
        $WindowsKits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
        $CandidateSignTools = @(Get-ChildItem -Path $WindowsKits -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
            Sort-Object FullName -Descending)
        if ($CandidateSignTools.Count -gt 0) {
            $SignTool = $CandidateSignTools[0].FullName
        }
    }
    if (-not $SignTool -or -not (Test-Path -LiteralPath $SignTool -PathType Leaf)) {
        throw "A signing thumbprint was supplied, but x64 signtool.exe was not found."
    }
    & $SignTool sign /sha1 $SignCertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $AppExecutable
    if ($LASTEXITCODE -ne 0) {
        throw "Authenticode signing failed for PolePosition.exe."
    }
} else {
    Write-Warning "No code-signing certificate was supplied. Windows may show an Unknown Publisher/SmartScreen warning."
}

$PylonHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PylonRuntime).Hash.ToLowerInvariant()
$GitCommit = "unknown"
try {
    $CandidateCommit = (& git -C $ProjectRoot rev-parse HEAD 2>$null).Trim()
    if ($CandidateCommit) {
        $GitCommit = $CandidateCommit
    }
} catch {
    $GitCommit = "unknown"
}
$Manifest = [ordered]@{
    application = "Pole Position"
    version = $Version
    architecture = "windows-x64"
    python = $PythonProbe.Trim()
    git_commit = $GitCommit
    built_at_utc = [DateTime]::UtcNow.ToString("o")
    pylon_runtime_file = [IO.Path]::GetFileName($PylonRuntime)
    pylon_runtime_sha256 = $PylonHash
    pylon_runtime_signer = $PylonSigner
    pylon_runtime_signature_status = [string]$PylonSignature.Status
    model_weights_included = $false
    full_training_runtime_included = $true
    torch_version = $TorchInfo.torch
    cuda_available = [bool]$TorchInfo.cuda
    cuda_version = $TorchInfo.cuda_version
}
$Manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $AppDirectory "BUILD-MANIFEST.json") -Encoding UTF8

$VerificationHome = Join-Path $BuildRoot "verification-station"
New-Item -ItemType Directory -Force -Path $VerificationHome | Out-Null
$PreviousHome = $env:POLE_POSITION_HOME
$env:POLE_POSITION_HOME = $VerificationHome
try {
    $Process = Start-Process -FilePath $AppExecutable -ArgumentList "--verify-install" -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        $CheckFile = Join-Path $VerificationHome "PolePosition-install-check.json"
        $Detail = if (Test-Path $CheckFile) { Get-Content -Raw $CheckFile } else { "No check report was written." }
        throw "Frozen application self-check failed with exit code $($Process.ExitCode). $Detail"
    }
} finally {
    $env:POLE_POSITION_HOME = $PreviousHome
}

$InstallerAssetDirectory = Join-Path $ScriptDirectory "installer-assets"
$AppIconFile = Join-Path $ProjectRoot "battery_inspector\assets\app_icon.ico"
$RequiredInstallerInputs = @(
    $AppDirectory,
    $InstallerAssetDirectory,
    $AppIconFile,
    $PylonRuntime
)
foreach ($RequiredInput in $RequiredInstallerInputs) {
    if (-not (Test-Path -LiteralPath $RequiredInput)) {
        throw "Required Inno Setup input does not exist: $RequiredInput"
    }
}
$InnoArguments = @(
    "/Qp",
    "/DAppVersion=$Version",
    "/DPylonRuntimeFile=$PylonRuntime",
    "/DFrozenAppDirectory=$AppDirectory",
    "/DReleaseOutputDirectory=$ReleaseRoot",
    "/DInstallerAssetDirectory=$InstallerAssetDirectory",
    "/DAppIconFile=$AppIconFile",
    $InnoScript
)
Push-Location $ScriptDirectory
try {
    Invoke-Checked $InnoCompiler $InnoArguments "Inno Setup compilation"
} finally {
    Pop-Location
}

$Installer = Join-Path $ReleaseRoot "Pole-Position-v$Version-Setup-x64.exe"
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "Inno Setup did not create the expected installer: $Installer"
}
if ($SignCertificateThumbprint) {
    & $SignTool sign /sha1 $SignCertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $Installer
    if ($LASTEXITCODE -ne 0) {
        throw "Authenticode signing failed for the Pole Position installer."
    }
}
$InstallerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Installer).Hash.ToLowerInvariant()
"$InstallerHash *$([IO.Path]::GetFileName($Installer))" | Set-Content -LiteralPath "$Installer.sha256" -Encoding ASCII

Write-Host ""
Write-Host "Pole Position Windows installer created successfully:" -ForegroundColor Green
Write-Host "  $Installer"
Write-Host "  $Installer.sha256"
Write-Host "  $ReleaseLock"
Write-Host ""
Write-Host "The installer is offline/self-contained and contains the complete training runtime."
Write-Host "Production ONNX/JSON and the training base checkpoint remain separate."
