# Polarity Tool v0.18.0

## Release target

v0.18.0 minimizes station storage while preserving actionable failure evidence.
The v0.17.1 settings hotfix and the bench-tested v0.17 inspection engine are
carried forward unchanged.

## Memory-first production inspection

- Production frames are captured, registered, cropped, classified, and rendered
  from in-memory image buffers.
- A production PASS writes no image evidence, manifest, SQLite inspection row,
  or per-cycle audit event.
- The latest PASS remains visible in Overview and Inspection Detail until another
  result replaces it.
- Session counts and the recent-result strip reset at application startup rather
  than persisting aggregate PASS history.

## Fail-only persistence and retention

- Every production non-PASS outcome is retained: REJECT, NOT READY, acquisition
  failure, and SYSTEM FAULT.
- Retained packages include the full frame, capture metadata, aligned/reference
  views when available, terminal/marking crops, diagnostics, and manifest.
- Settings → General adds independent age and capacity limits, defaulting to
  30 days and 5.0 GB. The oldest failures are removed first.
- Retention is scoped strictly to `runtime/inspections`; validation, recipe,
  model, and training assets are excluded.
- On first v0.18 startup, legacy production PASS evidence and database/audit rows
  are purged. Guided validation PASS data is preserved.

See [`STORAGE_POLICY.md`](STORAGE_POLICY.md) for the exact rules.

## Binary PLC result

- The PLC result contract is now `Pass` BOOL plus `Fail` BOOL. `FailCode` is no
  longer written or configured.
- While `Busy=1`, `Complete=0`, `Pass=0`, and `Fail=0`.
- At completion exactly one result bit is true: PASS for an accepted part, FAIL
  for every other outcome.
- Older `...FailCode` configuration names migrate to the corresponding `...Fail`
  name, but the Logix tag type must be changed from DINT to BOOL and verified
  before physical PLC commissioning.

See [`PLC_INTERFACE.md`](PLC_INTERFACE.md) for the truth table.

## Compatibility and schema

- Application version: `0.18.0`
- Manifest schema: `7`
- Inspection-record schema: `7`
- Inspection engine remains
  `reference_registration_terminal_face_guard_ml_v2`; validated recipe
  eligibility is unchanged.
- Camera, recipe, locator, classifier, terminal-face, ring, ML training, heartbeat,
  bypass, and v0.17.1 Settings Save & Apply behavior are otherwise unchanged.
