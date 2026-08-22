# Implementation and Acceptance Roadmap

## Completed in v0.21.0 - Recipe-controlled terminal finish

- SILVER/BRASS is configured independently for each physical terminal.
- The check is conventional reference-anchored color analysis, not a new ML
  polarity class and not a recipe-specific polarity model.
- Dark stamp grooves and bright glare are excluded from the chroma signature.
- Mismatch and no-decision outcomes fail closed before marking/ring acceptance.
- Inspection Detail and retained failure evidence include finish status,
  confidence, metrics, and current/reference comparison.
- Legacy active recipes remain compatible; new/edit revisions require finish
  selection and guided validation.

## Completed in v0.20.0 - Invalid-marking ML class

- New guided models contain PLUS, MINUS, BLANK, and INVALID_MARKING.
- A confident INVALID_MARKING prediction always produces a product reject.
- Low confidence remains NO DECISION; missing/invalid terminal faces remain the
  responsibility of the independent physical-input gate.
- Existing valid PLUS/MINUS/BLANK training samples are retained and included in
  the new dataset; no reset is performed.
- Existing three-class models remain usable for already-bound recipe revisions,
  while new revisions require a current four-class model.
- No geometry veto is introduced in this release.

## Completed in v0.19.0 - Authoritative PLC mode and numbered recipes

- PLC mode is configured only on the PLC tab; automatic simulation fallback and
  immediate simulation-switch actions are removed.
- Production inspection requests are limited to the configured PLC Trigger tag;
  Overview retains the explicit manual action.
- Every recipe has a stable positive number shared by all revisions.
- Existing recipe databases receive deterministic number assignments on upgrade.
- PLC recipe authority can compare a STRING name or integer recipe number.

## Completed in v0.18.0 - Memory-first production storage and binary PLC result

- Production frames, aligned images, crops, and diagnostics are processed and
  displayed from RAM.
- Production PASS writes no evidence, inspection row, or per-cycle audit event.
- REJECT, NOT READY, acquisition failure, and SYSTEM FAULT retain complete
  evidence and database history.
- Configurable failure age/capacity limits remove the oldest eligible evidence
  without traversing validation, recipe, model, or training assets.
- Legacy production PASS evidence/history is purged while validation PASS data
  is preserved.
- Yield counts and recent results are session-only.
- PLC output is mutually exclusive Pass/Fail BOOL data with no reason code.

## Completed in v0.6.0 - Milestone 1: Fresh acquisition and cycle control

- One trigger path for Manual, PLC Simulation, and pycomm3.
- Fresh `CameraFrame` identity/timestamps required for every cycle.
- No startup grading and no cached-image fallback.
- Explicit ACQUIRING / LOCATING / INSPECTING / SAVING / terminal state machine.
- Fresh per-cycle in-memory image/crop ownership (persistence policy updated in v0.18.0).
- PASS/REJECT counters exclude NOT READY and SYSTEM FAULT.
- Product and system outcomes are explicit in the HMI (PLC output simplified in v0.18.0).

## Completed in v0.6.0 - Milestone 2: Reference-backed recipe revisions

- New/edit recipe wizard begins with Reference.
- Capture, review, retake, accept, or explicitly retain existing reference.
- Battery/terminal/marking ROIs visibly taught on the accepted image.
- Reference hash, capture identity, camera profile, quality, and dimensions stored.
- Immutable revision-specific reference file.
- Edit resets validation and leaves active revision unchanged.
- Draft save allowed; production activation blocked until inspection readiness.

## Completed in v0.7.0 - Milestone 3: Battery position and rotation registration

- Reference-based OpenCV feature registration.
- SIFT with ORB fallback.
- RANSAC homography and full-resolution coordinate mapping.
- Terminal regions excluded from pose/orientation evidence.
- Independent 180-degree orientation comparison.
- Match count, inlier ratio, reprojection error, scale, rotation, visibility, and orientation metrics.
- Transformed battery, terminal, and marking polygons shown in the HMI.
- Bounds, scale, convexity, visibility, and mirroring checks.
- Explicit `BATTERY COULD NOT BE LOCATED`, never guessed PASS.

Representative production images are still required to determine whether any battery family needs a YOLO OBB locator.

## Completed in v0.7.0 - Milestone 4: Polarity recognition

- Per-recipe known-good reference-template classifier.
- PLUS, MINUS, BLANK, OTHER, and UNREADABLE outcomes.
- Crop quality, confidence, threshold, and class-score evidence.
- Successful validation crops added as additional templates.
- Low confidence/poor readability never converted to BLANK.
- Red-ring detection remains independent.
- Reversed-marking fixture is a permanent regression case.
- Optional geometric classifier retained for engineering evaluation.

A future ONNX model remains an option if representative-data testing requires a universal classifier.

## Completed in v0.7.0 - Milestone 5: Real recipe validation

- Repeated fresh inspections from the recipe wizard.
- Same camera, locator, classifier, ring detector, and evidence path as production.
- Known-good samples required in varied positions/rotations.
- Duplicate/nearly identical validation poses do not count.
- Every sample and terminal result stored as evidence.
- SHA-256 configuration fingerprint invalidates validation after a teach/settings change.
- DRAFT remains available when incomplete.
- Deliberate activation allowed only after all configured gates pass.

## Completed in v0.8.0 - Milestone 5A: Terminal-head rotation hardening and evidence export

- Central terminal-top localization inside each taught marking ROI.
- Independent 0-180 degree stamp-angle measurement.
- Rotation-invariant one-line MINUS and two-perpendicular-line PLUS geometry.
- Canonical current/reference template confirmation.
- Fail-closed geometry/template conflict and weak-confirmation behavior.
- Explicit reference-template fallback for unsupported terminal-top geometry.
- Per-terminal top, overlay, response, and canonical diagnostic images.
- Stamp geometry, angle, top-lock, and fallback shown in Inspection Detail.
- Open Evidence Folder and atomic Export Inspection ZIP actions.
- Application version, Git revision, engine, and schema identity in manifests.
- Exact real-cycle independent-stamp-rotation regression fixture and smoke test.

Representative production data is still required to qualify thresholds for every terminal family and supplier.



## Next - Milestone 6: PLC commissioning

Finalize with the site PLC program:

```text
IDLE -> TRIGGER EDGE -> BUSY -> RESULT/SEQUENCE -> COMPLETE -> ACK -> IDLE
```

Define and test:

- recipe ownership and requested-recipe behavior;
- trigger debounce and sequence IDs;
- result validity window;
- complete acknowledgement/reset;
- heartbeat/watchdog;
- timeout behavior;
- recovery after disconnect;
- binary Pass/Fail result timing;
- PLC simulation parity with the physical handshake.

## Next - Milestone 7: Representative-data qualification

For every battery family and supplier:

- collect known-good, reversed, wrong-ring, blank, worn, dirty, glare, damaged, and unreadable samples;
- cover full allowed X/Y, rotation, scale, focus, exposure, and lot variation;
- split qualification data by production lot/date rather than near-duplicate frames;
- establish false-accept, false-reject, unreadable, and localization-failure rates;
- tune recipe thresholds only from controlled qualification evidence;
- add YOLO OBB or ONNX only where the measured current implementation is insufficient.

## Milestone 8: Production hardening

- Role-based authentication/authorization.
- Recipe approval and revision rollback. Workstation backup/restore completed in v0.22.0.
- Alarm rationalization separate from product rejects.
- Failure-evidence monitoring and maintenance reporting.
- Watchdog/restart behavior.
- Installer/service and signed release process.
- Camera/lighting maintenance checks.
- FAT/SAT scripts and site acceptance records.

## Completed in v0.8.1 - Milestone 5B: Conditional terminal-top gate hardening

- Added nominal/conditional/rejected terminal-top gate states.
- Added fail-closed conditional acceptance for strong, centered PLUS/MINUS geometry only.
- Retained strict behavior for BLANK, center fallback, ambiguous geometry, partial visibility, and template conflicts.
- Added the exact cycle-000011 production regression fixture and smoke test.
- Added operator-visible `CONDITIONAL ACCEPT` diagnostics.
- Added Git archive revision substitution for source-only deployment evidence.

## Completed in v0.9.0 - ISA-101-aligned light HMI and scrollbar-free navigation

- Replaced the original dark palette with a controlled light neutral palette.
- Reserved red/amber/green for semantic exception/result use and made normal healthy operation neutral.
- Separated physical terminal-role colors from alarm/pass colors.
- Replaced long-page scrolling with recipe/event pagination, stacked terminal evidence, fixed settings tabs, and guided wizard pages.
- Added the controlled HMI philosophy, style guide, UI contract, and light-theme reference image.
- Added static regression tests to prevent dark-theme and page-scrollbar reintroduction.

## Completed in v0.9.1 - Persisted recipe compatibility hotfix

- Added a loader alias for the v0.8.1 conditional geometry-confidence field.
- Preserved current-key precedence when both legacy and canonical keys exist.
- Made persisted locator and classifier setting readers ignore unknown keys.
- Added an SQLite repository regression reproducing the v0.9.0 startup crash.
- Preserved recipes, references, validation evidence, camera configuration, and PLC configuration in place.

## Completed in v0.10.0 - Optional ML polarity classification

- Added generic ONNX Runtime production inference for PLUS/MINUS/BLANK/UNREADABLE.
- Isolated the central metal terminal top before inference so the red ring and molded case symbols are not ML shortcuts.
- Added confidence/margin and stricter center-fallback gates that fail closed to UNREADABLE.
- Bound ML recipe revisions to exact model ID/version/SHA-256 and guided-validation fingerprints.
- Added a fixed light-HMI Settings -> VISION / ML commissioning page.
- Added safe dataset export, train/val/test preparation, manual UNREADABLE labeling, Ultralytics classification training/export, and ONNX probing tools.
- Kept existing validated legacy recipe revisions operational until explicitly edited/revalidated for ML.

## Completed in v0.11.0 - Guided in-HMI ML training

- Added physical-camera sample capture directly from the HMI; production evidence folders are no longer required for dataset collection.
- Added adjustable terminal-top ROI preview with explicit PLUS/MINUS/BLANK/UNREADABLE labeling and duplicate protection.
- Added independent-capture counting and leakage-safe grouped train/validation/test preparation.
- Added training-runtime/CUDA detection and a background Ultralytics classification training worker.
- Added automatic ONNX export, held-out evaluation, and gated candidate installation into the station runtime model store.
- Preserved exact model SHA-256 binding and mandatory recipe revalidation for ML-bound revisions.

## Next - ML data qualification and PLC production contract

- Collect representative PLUS/MINUS/BLANK/UNREADABLE terminal-top crops across battery families, suppliers, lots, lighting variation, and terminal-head rotations using the guided ML Training page.
- Train the first candidate classifier and keep additional independent physical-battery challenge parts outside the built-in split.
- Establish site acceptance thresholds from measured false-accept, false-reject, and unreadable rates rather than convenience.
- Commission the candidate through recipe revisions and compare ML against the legacy geometry classifier during controlled trials.
- Finalize and commission the physical pycomm3 trigger/busy/complete/result acknowledgement contract with the PLC program.


## Completed in v0.12.0 - Multi-ROI global ML dataset capture

- One fresh camera frame can supply multiple labeled terminal-top ROIs.
- PLUS/MINUS/BLANK/UNREADABLE are assigned per ROI and committed as one logical capture batch.
- Same-frame crops remain grouped for leakage-safe dataset splitting.
- Optional battery/terminal-family metadata supports global dataset coverage review.
- The red-ring confirmation checkbox was removed from ML Training; red-ring inspection remains independent.
- Batch undo and backward-compatible sample metadata were added.
