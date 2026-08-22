# Polarity Tool v0.7.0 Release Notes

## Purpose

v0.7.0 moves the project from fresh-capture/recipe-reference infrastructure to a real evidence-backed inspection engine. It implements battery registration, marking recognition, and guided real validation while preserving the approved HMI layout.

## New inspection capabilities

### Reference feature registration

The active recipe reference is now used to locate the battery in a fresh camera frame. The locator supports translation, arbitrary in-plane rotation, moderate scale variation, and limited perspective change.

The locator reports and saves:

- detector type;
- reference/current feature counts;
- reliable matches and RANSAC inliers;
- inlier ratio;
- median reprojection error;
- scale X/Y and combined scale;
- rotation;
- visible fraction;
- perspective skew;
- 180-degree orientation scores and margin;
- transformed battery polygon and homography.

Terminal and marking ROIs are extracted from the perspective-aligned full-resolution battery image. The HMI overlays transformed polygons on the original camera image.

### Reference-template polarity classification

The default classifier compares each current marking crop with all marking classes taught by the current recipe. It supports PLUS, MINUS, and BLANK recipes and returns OTHER or UNREADABLE when appropriate.

Successful guided validation samples are retained as additional per-recipe templates. Classification evidence includes class scores, top/second scores, margin, same-terminal similarity, crop quality, and template count.

The red-ring check remains independent.

### Real recipe validation

The recipe wizard now acquires and grades real fresh samples. A successful sample counts only when the battery pose differs enough from earlier successful samples. All attempts remain in the audit evidence.

Any change to the accepted reference, orientation, ROIs, expected marks, ring requirements, or vision settings changes the configuration fingerprint and clears earlier validation.

A revision can always be saved as DRAFT. Activation requires the configured number of real passes and a clean production readiness check.

### Traceable marking-data export

Passing recipe-validation crops can be exported into PLUS, MINUS, and BLANK class folders with CSV/JSONL traceability:

```powershell
python scripts\export_marking_dataset.py --data-dir runtime --output dataset\markings --clean
```

The default export excludes production evidence, accepts only an overall PASS with a matching per-terminal result, and deduplicates image bytes by SHA-256.

## Demonstration behavior

The bundled simulation now has separate evidence:

- `demo_reference_good.png`: known-good reference with MINUS on the physical negative terminal and PLUS on the physical positive terminal.
- `demo_battery.jpg`: intentionally reversed inspection sample.

Running the bundled simulation must report:

```text
REJECT - POLARITY MARKINGS REVERSED
```

The known-good demo reference is never substituted for a user-captured reference.

The same regression can be run without starting PySide6:

```powershell
python scripts\vision_smoke_test.py
```

The command exits non-zero unless the known-good fixture passes and both the normal
and 180-degree-rotated reversed fixtures reject as `POLARITY MARKINGS REVERSED`.

## PLC result behavior

Relevant product codes include:

```text
0  PASS
1  POLARITY MARKINGS REVERSED
2  RED RING MISMATCH
3  UNREADABLE OR MARKING MISMATCH
4  BATTERY COULD NOT BE LOCATED
10 NO ACTIVE RECIPE
11 NO NEW CAMERA FRAME
12 INSPECTION NOT READY
13 INTERNAL/STATION FAULT
```

The final site PLC program still needs commissioning for acknowledgement, sequence IDs, and watchdog behavior.

## Upgrade notes

Existing user recipes remain readable. Recipes without explicit classifier settings migrate to `reference_template`. Edited revisions reset validation and must be revalidated before activation.

The bundled demonstration database migration only repairs references whose source is explicitly `BUNDLED_DEMO_REFERENCE`; physical/user-captured references are not modified. Only the controlled `GROUP31_XHD` regression fixture remains prevalidated. Other system-seeded layouts are demoted to DRAFT because they do not carry battery-family-specific validation evidence.

## Workstation acceptance

After upgrade:

1. Launch with PLC Simulation.
2. Confirm the bundled reversed sample reports REJECT, not PASS.
3. Connect the physical camera and create a new recipe from a verified known-good battery.
4. Move/rotate the battery through all required validation samples.
5. Activate the validated revision.
6. Run at least one fresh known-good battery and verify PASS.
7. Run known-bad reversed, wrong-ring, and unreadable samples and verify reject reasons.
8. Review full, aligned, reference, terminal, and marking evidence.
9. Confirm manual and PLC Simulation triggers both acquire a new frame.
10. Complete the full checklist in `COMMISSIONING_v0.7.0.md`.

## Validation in the build environment

- 77 automated tests pass.
- Full test execution completed in under one minute in this container.
- Python compilation passes.
- The bundled non-GUI vision regression passes.
- PySide6, the Basler camera, and the physical PLC are not available in this environment, so target-machine GUI and hardware commissioning remain required.
