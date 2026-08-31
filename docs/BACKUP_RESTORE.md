# Pole Position Workstation Backup and Restore

## Purpose

Use the workstation backup when moving Pole Position to a replacement PC,
before major computer service, or before a controlled station-state change.
The result is one portable ZIP. Do not edit files inside the ZIP.

## Three transfers, not one

The workstation backup moves a whole station. Two smaller packages move one
thing each, for the cases where both machines are staying:

| Transfer | Moves | Where |
| --- | --- | --- |
| **Workstation backup** | the entire station: settings, all recipes, ML data and models, audit history, retained evidence | Settings → STATION TRANSFER |
| **Model package** | one ONNX model and its manifest | Settings → POLARITY ML MODEL PACKAGE |
| **Recipe package** | one recipe revision with its reference image, validation evidence, and bound model | Recipes → EXPORT ▾ → Full recipe package |

Both packages are checksummed ZIPs: every member is listed in a manifest with
its SHA-256, and an import that does not match refuses rather than installing a
file that was damaged or changed after it was written.

The rest of this document is about the workstation backup. The two packages are
described in `docs/RECIPE_EDITING.md` and below under **Model and recipe
packages**.

## What the ZIP contains

- `config.json` station settings.
- Recipe database, recipe revisions, and audit history.
- Immutable recipe reference images and validation templates.
- ML training samples and the installed station models.
- Retained non-PASS inspection evidence.
- A manifest containing the application version, archive schema, original path
  roots, file sizes, and SHA-256 for every included file, plus the list of
  excluded path prefixes.

## What the ZIP deliberately omits

Two derived ML directories are excluded, because both are rebuilt from data the
backup does carry and together they can dominate the archive:

| Excluded | Rebuilt from |
| --- | --- |
| `ml_training/staging/` | Captures taken in the ML training page but not yet accepted. Accepting one copies it into the sample store, which is included. |
| `recipe_staging/` | Captures taken in the recipe wizard but not yet accepted. Accepting one copies it into an immutable recipe revision, which is included. |
| `ml_training/datasets/` | The prepared train/val/test split is copies of `ml_training/samples/`, which is included. Re-prepare it in the training wizard. |
| `ml_training/runs/` | Checkpoints and plots from past training. The model a station inspects with is the installed package under `models/`, which is included. |

The staging directories are usually the largest of these by a wide margin. A
staged capture is a full-resolution lossless frame, tens of megabytes, written
every time a technician takes one; on one station 104 of them reached 1.2 GB of
a 2.0 GB backup.

What this drops is a capture staged but never accepted, and a candidate trained
but never installed, since each exists only in those directories. **Accept a
capture and install a candidate before relying on a backup to carry either.**

Pole Position now removes staged captures older than seven days at startup and
records what it reclaimed in the event log, so the station stops accumulating
them. The window is far longer than any wizard session, so a capture you are
working on is never at risk. `ml_training/runs/` still has no sweep; delete run
directories once their candidate is installed or superseded.

To see the breakdown of an existing backup, including one written before this
exclusion existed:

```
python scripts/analyze_station_backup.py "C:\path\to\backup.zip"
```

Production PASS images and PASS history are not included because Pole Position
does not write them to disk. The Python environment, Basler pylon runtime,
camera driver, and pycomm3 installation are software prerequisites and are not
station data; install the approved Pole Position package on the destination PC
before restoring.

## If an import fails

A failed restore leaves the station exactly as it was, clears the pending-import
flag, and records what happened in `.pole_position_restore_result.json` beside
`config.json`. Correct the problem and import again; the station starts normally
in the meantime.

The staged copy is discarded on failure, so import the backup file again rather
than expecting the previous attempt to resume.

### Recovering a station stuck on an earlier failure

Builds before this behaviour existed left the flag in place when a restore
failed. Such a station re-attempts the same failing restore on every launch and
refuses to import anything else, because a pending import is already recorded.

**Reinstalling the application does not clear it.** Station data lives apart
from the program files -- under `C:\ProgramData\Pole Position` for an installed
station -- and the installer preserves that data deliberately. The flag is
station data.

```
python scripts\clear_pending_restore.py            # report what it finds
python scripts\clear_pending_restore.py --clear    # clear the flag
```

It locates the station root the same way the application does, checks
`%PROGRAMDATA%\Pole Position` for an installed station, reports why the last
restore failed, and removes only the flag and the staged copy it points at.
Recipes, models, evidence, and configuration are untouched. To do it by hand,
delete `.pole_position_restore_pending.json` from the station root.

## Rollback archives

Each successful restore first writes a full copy of the station as it was to
`restore_rollback\`, so a restore can be undone. Those are complete station
copies, and only the three most recent are kept; older ones are removed after a
restore succeeds. Files you place in that directory yourself are never removed.
A failed restore keeps its rollback archive.

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
8. Confirm the restored recipes, camera settings, PLC mode/address/tags, installed
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
- Recipe numbers/names and reference images for every product the PLC can name.
- Installed ONNX model identity and runtime check.
- One known-good and one known-reject validation cycle under the approved site
  procedure.

## Model and recipe packages

### Model package

**Settings → POLARITY ML MODEL PACKAGE → EXPORT MODEL PACKAGE** writes the ONNX
model and manifest this station is inspecting with. **IMPORT MODEL PACKAGE**
verifies one and installs it as the receiving station's model.

Installing a model revalidates nothing. A recipe revision stays bound to the
model SHA-256 it was validated against: one bound to a different hash keeps
failing closed until it is revalidated. Moving a model to a second station is
what makes recipes bound to that hash resolvable there.

### Recipe package

**Recipes → EXPORT ▾ → Full recipe package** writes one recipe revision with:

- the recipe payload, including its validation records;
- the reference image the station captured and accepted;
- the ONNX model and manifest, when the revision is ML-bound *and* this
  station's installed model is the one it is bound to. If the station has since
  installed a different model, the package says so and carries no model.

**IMPORT** on the Recipes page accepts either a package (`.zip`) or the older
geometry template (`.json`). The two are not interchangeable:

| | Geometry template (JSON) | Recipe package (ZIP) |
| --- | --- | --- |
| Carries geometry, ROIs, expected markings | yes | yes |
| Carries the reference image | no | yes |
| Carries validation evidence | no | yes |
| Destination must capture a new reference | yes | no |
| Destination must revalidate | yes | no |

**A recipe package carries validation evidence across machines as-is.** That
evidence was recorded on the exporting station's camera, lens, lighting, and
fixture, and nothing on the destination can confirm those match. This is a
deliberate decision so a qualified recipe can be put on a second line without
re-teaching it. What follows from it is yours to enforce:

- Import only onto a station of the same build — same camera model, lens,
  working distance, light, and fixture.
- The import dialog names the source station, the export time, the sample count,
  and whether the bound model matches the one installed here. Read it.
- Run one known-good and one known-bad part on the destination before releasing
  the recipe to production. This is the check that catches a lighting or
  fixture difference, and it is the only one.
- Both the export and the import are written to the audit log, with the source
  station and the evidence counts, so a recipe's provenance stays traceable.

A package can never overwrite an existing revision: a revision is an immutable
production record on both machines, so importing one the station already has is
refused. Export a new revision from the source station instead. A package whose
recipe number or name is already used by a *different* recipe on the
destination is also refused — one selector value names one product.
