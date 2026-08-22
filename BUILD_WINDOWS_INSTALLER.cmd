@echo off
setlocal

if "%~1"=="" (
  echo Usage:
  echo   BUILD_WINDOWS_INSTALLER.cmd "C:\Path\To\pylon_Runtime_x64.exe"
  echo.
  echo The official Basler pylon Runtime redistributable is embedded in the
  echo resulting Pole Position installer. Production and base-model weights
  echo remain separate.
  exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\build-installer.ps1" -PylonRuntime "%~1" -Clean
if errorlevel 1 (
  echo.
  echo Pole Position installer build FAILED.
  exit /b 1
)

echo.
echo Pole Position installer build completed.
exit /b 0
