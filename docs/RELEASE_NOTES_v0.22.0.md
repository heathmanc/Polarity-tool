# Pole Position v0.22.0

## Release target

v0.22.0 adds a safe workstation migration function that exports one ZIP and
imports one ZIP from **Settings / General**.

## Backup

- Creates a consistent SQLite snapshot while the station is idle.
- Includes settings, recipes, recipe/validation assets, ML training data,
  installed models, audit history, and retained failure evidence.
- Embeds a configured ML model/manifest even when they are outside the runtime
  directory.
- Publishes the ZIP only after it is complete and returns its SHA-256.
- Includes no production PASS history because PASS production data remains
  memory-only.

## Restore

- Rejects unsafe paths, duplicate members, encryption, unsupported schemas,
  missing/untracked files, invalid JSON/database content, oversize archives,
  and size or SHA-256 mismatches.
- Stages a valid import without changing the running station.
- Applies the restore at the next start before camera, PLC, repository, or ML
  services open.
- Preserves the destination workstation's selected data-directory location.
- Rebases old absolute runtime/project paths in configuration, SQLite JSON,
  JSON manifests, and JSONL ML catalogs.
- Creates a complete pre-restore rollback ZIP before replacing live data.
- Rolls the data-directory swap back if the restore cannot finish.

## Unchanged production contracts

- Inspection engine:
  `reference_registration_terminal_face_guard_ml_v2`.
- Manifest and inspection-record schemas remain `8`.
- Four ML classes remain PLUS, MINUS, BLANK, and INVALID_MARKING.
- Recipe-controlled SILVER/BRASS finish remains required for new revisions.
- PLC result remains binary Pass/Fail.
- Production PASS remains memory-only; retained non-PASS evidence still follows
  the configured retention limits.

Application version: `0.22.0`.
