@echo off
setlocal

rem Build a fully bundled Pole Position Windows application locally.
rem
rem Produces a self-contained folder containing Python, Qt, OpenCV, ONNX
rem Runtime, the pypylon and pycomm3 bindings, and the complete PyTorch /
rem Ultralytics training runtime. No Basler pylon Runtime Redistributable and
rem no Inno Setup are required.
rem
rem For the distributable offline installer use BUILD_WINDOWS_INSTALLER.cmd,
rem which needs the licensed pylon Runtime and Inno Setup 6.
rem
rem Any arguments are passed through, for example:
rem   BUILD_WINDOWS_APP.cmd -Clean
rem   BUILD_WINDOWS_APP.cmd -TorchIndexUrl https://download.pytorch.org/whl/cu128
rem   BUILD_WINDOWS_APP.cmd -SkipSelfCheck -Archive

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\build-local.ps1" %*
if errorlevel 1 (
  echo.
  echo Pole Position local application build FAILED.
  exit /b 1
)

echo.
echo Pole Position local application build completed.
exit /b 0
