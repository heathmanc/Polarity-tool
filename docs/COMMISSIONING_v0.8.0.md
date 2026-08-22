# v0.8.0 Camera, Recipe, and Stamp-Rotation Commissioning Checklist

This checklist is a practical station-commissioning aid. It does not replace the site's approved HMI, quality, safety, or validation procedures.

## 1. Record the software and station baseline

Record:

- application version and Git commit;
- camera model, serial, firmware, and pylon version;
- lens, working distance, aperture, and focus position;
- lighting type, position, intensity, and trigger behavior;
- camera profile: resolution, pixel format, exposure, gain, frame rate, and trigger source;
- PLC program/revision and tag contract;
- recipe ID/revision and reference-image SHA-256.

Run:

```powershell
python scripts\camera_probe.py --grab
python scripts\vision_smoke_test.py
python scripts\stamp_rotation_smoke_test.py
```

All smoke tests must exit successfully before product qualification begins.

## 2. Verify fresh acquisition and cycle ownership

Using Manual Trigger and PLC Simulation:

1. Place a clearly distinguishable object in view.
2. Trigger an inspection.
3. Record cycle ID, frame ID, timestamp, and evidence path.
4. Move or replace the object.
5. Trigger again.
6. Confirm the image, frame identity, and evidence directory changed.
7. Block/disconnect the camera and confirm the previous image is not reused.

Acceptance: every cycle owns a new frame; acquisition failure cannot become PASS.

## 3. Create or revise a recipe from a controlled known-good battery

1. Select Recipes -> New Recipe or Edit / New Revision.
2. Capture a fresh full-resolution reference or explicitly keep the approved prior reference.
3. Review exposure, focus, clipping, and complete battery visibility.
4. Confirm the battery boundary and unique 180-degree orientation reference.
5. Teach the physical negative terminal search ROI.
6. Confirm the smaller marking ROI includes the complete terminal top and enough margin for local circle detection.
7. Teach the physical positive terminal and marking ROI.
8. Select the expected PLUS, MINUS, or BLANK independently for each physical terminal.
9. Set red-ring requirements independently.
10. Save as DRAFT until all qualification evidence is complete.

Do not use a detected mark or red ring to define physical terminal identity.

## 4. Verify transformed ROIs

Move and rotate the battery through the complete allowed station area. For every sample, review:

- battery polygon;
- terminal search polygons;
- marking polygons;
- battery-registration match/inlier counts;
- reprojection error;
- scale and rotation;
- visible fraction and orientation score.

Acceptance: both terminal and marking ROIs track the actual battery without clipping the required terminal-top area.

## 5. Verify terminal-top localization

For both terminals, review Inspection Detail and evidence:

- terminal-top detection method;
- center and radius;
- detection confidence;
- selected stamp-analysis bounds;
- overlay position relative to the physical circular top.

The selected circle must cover the central terminal top, not the outer hex, washer, red ring, or nearby case feature.

If the HMI reports `FALLBACK`, treat that terminal family as not yet qualified for rotation-invariant hybrid use until representative evidence demonstrates acceptable fallback performance.

## 6. Challenge independent terminal-head rotation

Threaded terminal heads may rotate independently of the battery body. Build a controlled test set covering, where physically possible:

- at least four substantially different MINUS angles;
- at least four substantially different PLUS angles;
- battery case held in approximately the same pose while terminal-head angle changes;
- battery case rotation while terminal-head angle remains similar;
- normal supplier/lot variation.

For each cycle, verify:

- physical terminal identity remains tied to recipe geometry;
- measured stamp angle changes as expected;
- MINUS remains MINUS through 180-degree periodicity;
- PLUS remains PLUS through 90-degree symmetry;
- red-ring measurement remains independent;
- class confidence and template confirmation remain above approved gates.

Acceptance: no correct part false-rejects solely because the threaded top stamp changed angle.

## 7. Guided validation

Run the required validation captures with known-good batteries. Vary:

- X/Y position;
- battery rotation;
- terminal-head stamp rotation;
- normal focus/exposure tolerance;
- representative battery samples and production lots.

A nearly duplicate battery pose does not increase the validation count. Review all retained attempts, including failures.

Acceptance: all required varied samples pass; no poor-quality or ambiguous stamp is converted to BLANK or PASS.

## 8. Known-bad challenge testing

At minimum test:

- reversed top markings;
- red ring on the wrong physical terminal;
- missing required red ring;
- wrong marking/unexpected symbol;
- genuine BLANK where BLANK is expected;
- PLUS or MINUS where BLANK is expected;
- scratch that resembles one line;
- intersecting scratches that resemble PLUS;
- worn, damaged, dirty, glare-obscured, or partially stamped marks;
- terminal top partly outside the marking ROI;
- battery partly out of frame;
- unrelated object/no battery.

Expected behavior:

```text
Known-good                       PASS
Reversed markings               REJECT - POLARITY MARKINGS REVERSED
Ring defect                     REJECT - RED RING MISMATCH
Unreadable/ambiguous stamp      REJECT - UNREADABLE MARKING
Wrong/unexpected marking        REJECT - TERMINAL MARKING MISMATCH
Battery not located             REJECT - BATTERY COULD NOT BE LOCATED
Camera/station failure          SYSTEM FAULT or NOT READY, never PASS
```

## 9. Evidence and export review

For representative passes and every failure type, select **EXPORT INSPECTION ZIP**. Verify the package includes, where applicable:

- full image and capture metadata;
- aligned/reference battery images;
- terminal and marking crops;
- reference marking crops;
- terminal-top crops;
- stamp overlays;
- stamp-response images;
- canonical stamp images;
- manifest with software/build identity;
- registration, geometry, template, hybrid, and ring measurements;
- final disposition and reason.

Confirm evidence paths, retention, disk alerts, and backup requirements meet site policy.

## 10. PLC commissioning

Only after vision qualification:

- verify requested-recipe ownership;
- verify one trigger edge starts one cycle;
- verify Busy asserts before grading;
- verify result/failure code and sequence remain valid until acknowledgement;
- verify trigger must return false before rearming;
- verify complete reset, heartbeat/watchdog, timeout, disconnect, and reconnect behavior;
- verify Manual, PLC Simulation, and physical PLC triggers use the same acquisition and grading path.

## 11. Qualification record

Document:

- application version/commit and manifest schema;
- camera/lens/lighting configuration;
- recipe and reference hash;
- terminal-top/stamp angle coverage;
- production lots and sample counts;
- known-good and known-bad results;
- false-accept, false-reject, unreadable, and localization-failure criteria;
- approvers and date.
