# Pole Position v0.21.1

## Release target

v0.21.1 renames the battery-inspection application **Pole Position** and adds a
new deployable mascot icon. Inspection, recipe, ML, PLC, and storage behavior is
unchanged from v0.21.0.

## New visual identity

- A charcoal industrial battery stands in first place on a gold winner's
  podium.
- Its red positive terminal is held aloft in a triumphant gesture.
- A small green check-mark badge communicates accepted inspection.
- The transparent master artwork is packaged at 1254 × 1254.
- Qt uses a 512 × 512 transparent PNG.
- Windows packaging can use the included multi-resolution ICO containing
  256, 128, 64, 48, 32, and 16 px images.

The icon deliberately uses a bold silhouette and restrained secondary detail
so it remains recognizable in the 58 px HMI header and at desktop-icon sizes.

## Renamed surfaces

- Main window title and HMI header.
- Help and exit dialogs.
- Qt application/display identity and global window icon.
- Build and retained-evidence application metadata.
- Current documentation, package metadata, and release artifacts.
- New `pole-position` console launcher.

## Compatibility

- Python imports remain under `battery_inspector`.
- Runtime paths and `battery_inspector.db` are unchanged.
- Existing configuration, recipes, validation, models, training data, and
  retained failure evidence remain in place.
- The original `battery-inspector` console launcher remains as a compatibility
  alias.
- Inspection engine remains
  `reference_registration_terminal_face_guard_ml_v2`.
- Manifest and inspection-record schemas remain `8`.
- Application version: `0.21.1`.
