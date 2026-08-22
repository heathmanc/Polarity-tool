# Pole Position v0.23.0

v0.23.0 adds the controlled Windows deployment system. Inspection decisions,
the v0.21 terminal-finish gate, recipe/model validation rules, the v0.19 PLC
contract, v0.18 fail-only retention, and v0.22 workstation backup/restore are
unchanged.

## Windows program and installer

- PyInstaller freezes the x64 Python 3.11 application as a Windows program.
- Inno Setup creates one offline, per-machine installer.
- Python, PySide6, Qt, OpenCV, NumPy, ONNX/ONNX Runtime, pypylon, pycomm3,
  PyTorch, torchvision, Ultralytics, and training/export dependencies are
  included.
- The official Basler pylon USB runtime and camera driver supplied to the build
  are embedded and installed silently.
- No production ONNX/JSON package or PyTorch base checkpoint is bundled.
- The build rejects accidental `.onnx`, `.pt`, or `.pth` inclusion.

## Installed data boundary

- Program files are installed under `C:\Program Files\Pole Position`.
- Mutable station state is stored under `C:\ProgramData\Pole Position`.
- Upgrade and uninstall operations preserve configuration, recipes, validation
  evidence, ML samples/models, audits, and retained failure evidence.
- Source/bench launches retain their existing repository-local configuration
  and runtime layout.

## Installation verification

- A noninteractive frozen-build check verifies resources and packaged
  dependencies without requiring a model, camera, or PLC.
- The result is retained as `PolePosition-install-check.json` in ProgramData.
- Build output includes a complete pip package inventory, build manifest, pylon
  runtime SHA-256, and installer SHA-256.

See `docs/WINDOWS_INSTALLER.md` for the controlled build and deployment
procedure.
