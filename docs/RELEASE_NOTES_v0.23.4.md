# Pole Position v0.23.4

v0.23.4 is a focused final-installer compilation correction based on v0.23.3.
Inspection, PLC, recipe, model, training, backup, and production-storage
behavior are unchanged.

## Corrected

- The build script passes absolute paths for the frozen application, installer
  output, installer assets, application icon, and Basler redistributable to
  Inno Setup.
- The Inno script no longer depends on the PowerShell process's working
  directory when resolving build inputs.
- Every required Inno input is checked before compiler launch, so a missing
  resource now reports its exact path.
- The compiler is launched from the Inno script directory as an additional
  compatibility safeguard.

This fixes the generic `The system cannot find the path specified` failure that
occurred after the PyInstaller build and frozen-application validation passed.
