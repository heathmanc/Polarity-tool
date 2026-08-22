# Build Notes - v0.23.4

v0.23.4 provides a Windows x64 PyInstaller/Inno Setup build. The offline installer
contains all station and guided-training packages plus the supplied official
Basler pylon USB runtime. It deliberately excludes production and base-model
weights. Frozen binaries use Program Files for read-only resources and
ProgramData for writable station state. See `docs/WINDOWS_INSTALLER.md`.

## Carried forward from v0.22.0

v0.22.0 adds verified workstation migration backup and restore. The inspection
engine, recipe validity, four-class ML contract, terminal-finish gate, storage
policy, and binary PLC result contract are unchanged.

## Workstation migration

- Settings / General exports one portable ZIP.
- Settings, the SQLite recipe/audit database, recipe and validation assets, ML
  samples and installed model, and retained failure evidence are included.
- Every archive path, size, and SHA-256 value is verified before restore.
- Unsafe, encrypted, malformed, oversized, damaged, or incomplete ZIPs fail
  without changing live station data.
- Restore is staged while the HMI is running and applied before services or the
  database open on the next launch.
- Absolute paths from the old workstation are rebased to the new project and
  data directories, including recipe, validation, evidence, and ML metadata.
- The new PC's selected data-directory location is preserved.
- A complete pre-restore rollback ZIP is created before replacement begins.
- Production PASS images remain absent because the station never stores them.

## Pole Position identity

- Window title: `Pole Position — Battery Polarity Inspection`.
- HMI header: `POLE POSITION` with the new transparent mascot icon.
- Help, exit, Qt application identity, build metadata, documentation, package
  identity, and release filenames use Pole Position.
- A 512 px transparent PNG is used by Qt; a multi-resolution 16–256 px Windows
  ICO and the generated 1254 px master are packaged with the application.
- The internal `battery_inspector` module and runtime/database paths are not
  renamed, preventing a cosmetic update from separating the station from its
  existing configuration, recipes, models, and evidence.
- The new `pole-position` console launcher is added while the original
  `battery-inspector` alias remains available for existing shortcuts.

## Carried forward from v0.21.0

v0.21.0 adds a recipe-controlled SILVER/BRASS visible-finish gate without
changing the four-class marking model, inspection engine identity, or binary PLC
result contract.

## Terminal finish gate

- New and edited recipes require SILVER or BRASS for both primary terminals.
- The current marking-circle crop is compared with the same terminal in the
  accepted known-good reference.
- Median Lab chroma and HSV saturation are measured inside the terminal face
  after dark engraving and bright glare are excluded.
- A clear opposite-material shift is `TERMINAL FINISH MISMATCH`.
- Borderline or malformed evidence is `TERMINAL FINISH NO DECISION`.
- Both states fail closed and retain the current/reference comparison for
  Inspection Detail and failure evidence.
- This verifies visible appearance under the commissioned camera/lighting; it
  does not identify material chemistry.

## Four-class guided model

- New training uses `PLUS`, `MINUS`, `BLANK`, and `INVALID_MARKING`.
- Existing PLUS/MINUS/BLANK samples remain in the training store.
- The guided capture/review/prepare/train/evaluate workflow handles the fourth
  class end to end.
- Offline preparation, training, and evaluation utilities use the same class
  contract.

## Production decision

- Terminal face must be present.
- Configured terminal finish must match.
- A confident `INVALID_MARKING` prediction is evaluated and always fails recipe
  comparison with the explicit inspection reason `INVALID MARKING`.
- Low confidence or insufficient margin remains `NO DECISION`.
- The physical terminal-face gate still handles missing terminals before ML.
- No geometry veto is enabled.

## Compatibility

- Existing active recipes without a finish field load as `NOT CONFIGURED` and
  keep their prior behavior until edited.
- Every new or edited revision requires finish selection and fresh guided
  validation before activation.
- Existing three-class models remain loadable for recipe revisions already
  bound to them.
- New or edited recipe revisions can bind only to the current four-class model.
- Application: 0.23.4
- Manifest schema: 8
- Record schema: 8
- Inspection engine:
  `reference_registration_terminal_face_guard_ml_v2` (unchanged to preserve
  existing validated-recipe fingerprints)
