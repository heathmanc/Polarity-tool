# Pole Position v0.23.1

v0.23.1 is a focused Windows installer-build correction based on v0.23.0.
Inspection, recipe, PLC, training, backup, and production-storage behavior are
unchanged.

## Corrected

- The Windows build script now defaults to `python`, so an activated Python
  3.11 virtual environment is honored naturally.
- `-PythonCommand` continues to accept an explicit full path to `python.exe`.
- The Python architecture/version probe no longer contains nested command-line
  quotes that Windows PowerShell 5.1 could remove. This fixes the invalid
  `print(f{...})` syntax error seen before dependency installation began.
- Regression coverage now prevents the fragile quoted f-string probe from
  returning.

The build still creates its own isolated environment under `build\windows`.
The active or explicitly selected Python interpreter is used only to validate
Python 3.11 x64 and create that controlled build environment.
