#ifndef AppVersion
  #define AppVersion "0.31.1"
#endif
#ifndef PylonRuntimeFile
  #error PylonRuntimeFile must name the official Basler pylon Runtime redistributable.
#endif
#ifndef FrozenAppDirectory
  #error FrozenAppDirectory must name the absolute PyInstaller output directory.
#endif
#ifndef ReleaseOutputDirectory
  #error ReleaseOutputDirectory must name the absolute installer output directory.
#endif
#ifndef InstallerAssetDirectory
  #error InstallerAssetDirectory must name the absolute installer asset directory.
#endif
#ifndef AppIconFile
  #error AppIconFile must name the absolute Pole Position icon file.
#endif
#ifndef CompressionThreads
  #define CompressionThreads "2"
#endif

#define AppName "Pole Position"
#define AppPublisher "Pole Position"
#define AppExeName "PolePosition.exe"

[Setup]
AppId={{DA39725C-7C6D-4E60-A73A-0D6F84EEDCF2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf64}\Pole Position
DefaultGroupName=Pole Position
DisableProgramGroupPage=yes
OutputDir={#ReleaseOutputDirectory}
OutputBaseFilename=Pole-Position-v{#AppVersion}-Setup-x64
SetupIconFile={#AppIconFile}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
; The payload is the whole frozen station plus the CUDA training runtime, which
; runs to several gigabytes. LZMA2 compresses with one thread by default, and a
; solid ultra64 stream over that much data takes hours on a single core with the
; compiler printing nothing the entire time. Extra block threads keep the
; compression level and cost a low single-digit percentage of ratio.
;
; Thread count is bounded by address space, not by the machine. ISCC is a
; 32-bit process, and an ultra64 stream uses a 64 MB dictionary whose encoder
; needs roughly ten times that -- about 700 MB per block thread. Four threads
; exceeded what a 32-bit process can address and the compile aborted with "Out
; of memory" on a workstation with 64 GB installed. Two is the default here;
; pass -CompressionThreads to build-installer.ps1 to change it, and use 1 if a
; build still runs out.
LZMAUseSeparateProcess=yes
LZMANumBlockThreads={#CompressionThreads}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=force
RestartApplications=no
WizardStyle=modern
MinVersion=10.0.19045
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Pole Position battery polarity inspection installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Dirs]
Name: "{commonappdata}\Pole Position"; Permissions: users-modify; Flags: uninsneveruninstall
Name: "{commonappdata}\Pole Position\models"; Permissions: users-modify; Flags: uninsneveruninstall
Name: "{commonappdata}\Pole Position\runtime"; Permissions: users-modify; Flags: uninsneveruninstall
Name: "{commonappdata}\Pole Position\runtime\models\training"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
Source: "{#FrozenAppDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#InstallerAssetDirectory}\MODEL_INSTALLATION.txt"; DestDir: "{commonappdata}\Pole Position\models"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "{#InstallerAssetDirectory}\clean_baseline_v017.json"; DestDir: "{commonappdata}\Pole Position\runtime"; DestName: ".clean_baseline_v017.json"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "{#PylonRuntimeFile}"; DestDir: "{tmp}"; DestName: "BaslerPylonRuntime.exe"; Flags: deleteafterinstall

[Icons]
Name: "{autoprograms}\Pole Position"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExeName}"
Name: "{autodesktop}\Pole Position"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\BaslerPylonRuntime.exe"; Parameters: "/passive /install=Cpp_Runtime;USB_Runtime;USB_Camera_Driver /allowupgrade"; StatusMsg: "Installing the Basler USB3 camera runtime..."; Flags: waituntilterminated
Filename: "{app}\{#AppExeName}"; Parameters: "--verify-install"; WorkingDir: "{app}"; StatusMsg: "Verifying the Pole Position installation..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#AppExeName}"; Description: "Launch Pole Position"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Mutable station data is deliberately preserved for repair/reinstall and can
; be removed only through an explicit workstation decommissioning procedure.

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWin64 then
  begin
    MsgBox('Pole Position requires 64-bit Windows 10 or Windows 11.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox(
      'Pole Position application files were removed. Station configuration, recipes, models, training data, and retained failure evidence remain in:' + #13#10 + #13#10 +
      ExpandConstant('{commonappdata}\Pole Position'),
      mbInformation,
      MB_OK
    );
end;
