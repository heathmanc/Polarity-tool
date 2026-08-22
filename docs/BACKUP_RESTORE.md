# Pole Position Workstation Backup and Restore

## Purpose

Use the workstation backup when moving Pole Position to a replacement PC,
before major computer service, or before a controlled station-state change.
The result is one portable ZIP. Do not edit files inside the ZIP.

## What the ZIP contains

- `config.json` station settings.
- Recipe database, recipe revisions, and audit history.
- Immutable recipe reference images and validation templates.
- ML training samples, datasets/runs that remain in the runtime, and installed
  station models.
- Retained non-PASS inspection evidence.
- A manifest containing the application version, archive schema, original path
  roots, file sizes, and SHA-256 for every included file.

Production PASS images and PASS history are not included because Pole Position
does not write them to disk. The Python environment, Basler pylon runtime,
camera driver, and pycomm3 installation are software prerequisites and are not
station data; install the approved Pole Position package on the destination PC
before restoring.

## Export

1. Stop production and wait until the station is idle.
2. Open **Settings / General**.
3. Select **EXPORT WORKSTATION BACKUP**.
4. Choose the destination and confirm.
5. Wait for **BACKUP COMPLETE**. Record or retain the displayed SHA-256 with the
   ZIP when required by the site's document-control process.
6. Copy the ZIP to approved storage or the replacement PC.

The export uses SQLite's backup interface for a consistent database snapshot.
Pole Position writes the ZIP to a temporary filename and publishes the final
file only after the archive is complete.

## Import and restore

1. Install the same or a compatible newer Pole Position release on the new PC.
2. Open **Settings / General**.
3. Select **IMPORT WORKSTATION BACKUP** and choose the exported ZIP.
4. Confirm staging. Pole Position checks archive paths, encryption state,
   schema, required files, sizes, SHA-256 values, configuration JSON, and the
   SQLite database. Live data is not changed during this check.
5. When **RESTORE VERIFIED AND STAGED** appears, close Pole Position.
6. Start Pole Position again. Before camera, PLC, ML, or database services open,
   the application creates a rollback ZIP and applies the restore.
7. A completion message identifies the rollback ZIP.
8. Confirm the active recipe, camera settings, PLC mode/address/tags, installed
   model, and system readiness before returning the station to production.

Stored absolute paths under the old runtime/project roots are rewritten for the
new PC. The destination PC's configured data-directory location remains
authoritative. A configured ML model outside the old runtime is embedded in the
ZIP and restored under the new runtime model directory.

## Failure behavior

- A failed verification does not create a pending restore.
- A failed startup restore keeps or returns the previous live data and leaves
  the pending package available for diagnosis.
- A pre-restore ZIP is created under `restore_rollback` before live data is
  replaced.
- Only one restore may be pending at a time.
- ZIP path traversal, duplicate members, encrypted members, untracked files,
  checksum mismatches, unsupported schemas, and archives exceeding safety
  limits are rejected.

## Important commissioning checks

A workstation backup transfers station data; it does not prove the replacement
PC's hardware installation. After restore, verify:

- Basler pylon and the first-available camera connection.
- Camera exposure, gain, resolution, and a fresh test capture.
- PLC mode, Logix path, tag types/names, heartbeat, and binary Pass/Fail
  handshake.
- Active recipe number/name and reference image.
- Installed ONNX model identity and runtime check.
- One known-good and one known-reject validation cycle under the approved site
  procedure.
