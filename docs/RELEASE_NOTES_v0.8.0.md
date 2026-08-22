# Polarity Tool v0.8.0 Release Notes

## Purpose

v0.8.0 addresses a false reject discovered in real inspection cycle `CYCLE-20260819-140953-386559-000006`. Battery registration was accurate, both terminal crops were correct, and the red-ring checks passed, but the negative MINUS stamp had rotated independently of the terminal used for the recipe reference. The v0.7 classifier searched only a small residual angle around the battery-aligned reference and therefore reported `NO_TAUGHT_CLASS_MATCH`.

The new engine treats battery pose and terminal-head stamp angle as separate quantities.

## Rotation-invariant hybrid classifier

The default classifier is now identified as:

```text
ROTATION_INVARIANT_HYBRID_V2
```

The inspection manifest identifies the complete engine as:

```text
reference_registration_rotation_invariant_hybrid_v2
```

### Terminal-top localization

Inside each taught marking ROI, the engine searches for the central circular terminal top and assigns candidates using:

- proximity to the expected crop center;
- plausible radius;
- circular edge support;
- full visibility inside the crop.

The selected center and radius define a smaller stamp-analysis region that excludes most outer hex, washer, knurling, and red-ring features. If no credible circle is found, the engine records `CENTER_FALLBACK` and uses the fail-closed reference-template compatibility path.

### Geometric stamp observation

The normalized central crop is enhanced with CLAHE and local-background removal. The engine measures line response over the complete 0-180 degree range and evaluates:

- primary line angle and signal;
- approximately perpendicular line signal;
- orthogonal ratio;
- primary-line center offset;
- line-intersection offset;
- contrast, sharpness, and clipping.

The independent geometric observation is:

```text
MINUS  one centered dominant line
PLUS   two centered approximately perpendicular lines
BLANK  no meaningful line response
UNREADABLE / AMBIGUOUS  quality or geometry gate not met
```

This stage does not know the recipe's expected class.

### Canonical template confirmation

The current and reference stamps are canonicalized using their independently measured angles. Template evidence is then used to confirm the geometric class and guard against scratches or unrelated surface features.

The engine rejects rather than guessing when:

- geometry and a strong taught-template result conflict;
- the observed geometry class is not taught by the recipe;
- template confirmation is below the configured minimum;
- hybrid confidence or class separation is below the recipe gate;
- the image-quality gate fails.

For terminal families where terminal-top localization is unavailable, the original reference-template decision remains as an explicitly recorded compatibility fallback.

## Exact real-cycle regression

The following current/reference crops are bundled as a permanent regression fixture:

```text
tests/fixtures/cycle_000006/
    negative_current.png
    negative_reference.png
    positive_current.png
    positive_reference.png
    negative_terminal.png
    positive_terminal.png
```

The negative current/reference MINUS stamps differ by more than 40 degrees after periodic normalization. v0.8.0 must still classify both as MINUS.

Run:

```powershell
python scripts\stamp_rotation_smoke_test.py
```

Expected outcome:

```text
NEGATIVE ... detected=MINUS ... result=PASS
POSITIVE ... detected=PLUS  ... result=PASS
Overall smoke-test status: PASS
```

The automated suite also rotates each real current crop through multiple arbitrary angles and verifies that class identity is retained.

## New diagnostic evidence

For each evaluated terminal, the pipeline can now save:

```text
<terminal>_terminal_top.png
<terminal>_stamp_overlay.png
<terminal>_stamp_response.png
<terminal>_canonical_stamp.png
```

The terminal record and manifest include:

- terminal-top center and normalized center;
- terminal-top radius and radius fraction;
- detection method, candidate count, and confidence;
- stamp-analysis bounds;
- primary and orthogonal angles;
- measured stamp angle;
- primary/orthogonal signals and ratio;
- line/intersection offsets;
- geometry class, confidence, scores, and status;
- template scores and best per-class match details;
- canonical rotation, residual rotation, and shift;
- hybrid scores, margin, and final status.

## HMI evidence workflow

Inspection Detail now displays:

- independent stamp-geometry result;
- measured stamp angle;
- terminal-top lock confidence and fallback status;
- existing expected/detected/confidence/ring information.

It also exposes:

```text
OPEN EVIDENCE FOLDER
EXPORT INSPECTION ZIP
```

ZIP creation is atomic and includes the complete cycle folder as one traceable package.

## Manifest/build identity

Manifest schema and inspection-record schema are now version 4. Every new manifest includes:

```json
{
  "software": {
    "application": "Polarity Tool",
    "application_version": "0.8.0",
    "git_commit": "<best-effort revision>",
    "inspection_engine": "reference_registration_rotation_invariant_hybrid_v2",
    "manifest_schema_version": 4,
    "record_schema_version": 4
  }
}
```

A packaged station can set `POLARITY_TOOL_GIT_COMMIT` during deployment; otherwise a local Git checkout is queried on a best-effort basis.

## Recipe compatibility

Existing recipes remain readable. New hybrid settings receive conservative defaults during deserialization. Because classifier settings are included in the recipe-validation fingerprint, any technician edit followed by save creates a configuration-specific validation state.

Current production recipes should still be challenged and requalified with representative terminal-head rotations. A successful software regression is not a substitute for site validation.

## Workstation acceptance

After upgrading:

1. Run `python scripts\vision_smoke_test.py`.
2. Run `python scripts\stamp_rotation_smoke_test.py`.
3. Launch with PLC Simulation.
4. Inspect the same physically correct battery at several terminal-head stamp angles where possible.
5. Confirm the MINUS and PLUS remain correct while battery registration remains stable.
6. Open Inspection Detail and verify stamp geometry, angle, and terminal-top lock are populated.
7. Export the inspection ZIP and verify full, terminal, marking, terminal-top, overlay, response, canonical, and manifest evidence are present.
8. Challenge with reversed markings, wrong ring, damaged stamp, glare, and no-battery conditions.
9. Do not activate or retain a recipe as production-qualified until the full commissioning checklist is complete.

## Validation in the build environment

The release build runs:

- the complete automated test suite;
- Python compilation checks;
- the exact cycle-000006 smoke test;
- the existing known-good/reversed/180-degree vision smoke test;
- source-archive extraction tests;
- Git-bundle clone and history verification.

PySide6 rendering, the physical Basler camera, and the site Allen-Bradley PLC are not available in the build container. Target-machine GUI, camera, lighting, timing, and PLC commissioning remain required.
