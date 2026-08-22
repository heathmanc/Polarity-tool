# Pole Position v0.23.2

v0.23.2 is a focused Windows packaging correction based on v0.23.1.
Inspection, PLC, recipe, model, training, backup, and production-storage
behavior are unchanged.

## Corrected

- The PyInstaller specification excludes ONNX's backend conformance tests and
  their bundled `.onnx` fixtures. Pole Position still includes the ONNX modules
  required for model export and validation.
- The build script also removes only those exact ONNX test directories from
  the temporary frozen output as defense against third-party hooks collecting
  them again in a future package version.
- The no-model-weights release gate remains fail-closed for production and
  training weights, while the unused ONNX package test corpus is no longer
  collected in the first place.
- Unexpected-model reporting is capped to a readable preview instead of
  flooding the PowerShell console with the entire file list.
- Automatic Inno Setup discovery now includes the standard per-user
  `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` installation used by some
  `winget` installations.
