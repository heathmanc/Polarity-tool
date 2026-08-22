# v0.7.0 Camera and Recipe Commissioning Checklist

This checklist is for engineering/maintenance commissioning. It is not a substitute for site FAT/SAT or quality approval.

## 1. Start in a safe commissioning state

- Run `python scripts\vision_smoke_test.py` and confirm the bundled regression reports PASS.
- Use PLC Simulation until the vision recipe is qualified.
- Confirm the physical reject mechanism is disabled or safely controlled during dry runs.
- Verify the camera mount, lens focus, aperture, and lighting are mechanically locked.
- Confirm the camera reports the expected active acquisition resolution.
- Confirm exposure/gain/pixel format settings can be applied and a fresh test frame is shown.

## 2. Verify fresh capture

1. Place a distinctive object in view.
2. Run Manual Inspection.
3. Record the cycle ID, frame ID, timestamp, and saved `full.jpg`.
4. Move the object and trigger again.
5. Confirm the IDs/timestamp changed and the second image contains the new scene.
6. Repeat with SEND TEST PLC TRIGGER.
7. Block/disconnect the camera and confirm the previous image is not reused.

Acceptance: every trigger owns a new frame, and acquisition failure never becomes PASS.

## 3. Create a recipe from a verified known-good battery

1. Select Recipes -> New Recipe.
2. Enter the controlled recipe name and part number.
3. Place a documented known-good battery in the station.
4. Capture the reference.
5. Review image quality and select USE THIS IMAGE. Retake if needed.
6. Confirm the battery boundary surrounds only the intended battery case.
7. Confirm the orientation reference uses unique case/notch/label geometry when the case can appear 180 degrees reversed.
8. Teach the physical negative terminal search ROI.
9. Confirm the smaller marking ROI includes the complete top stamp with margin.
10. Teach the physical positive terminal and marking ROIs.
11. Set expected marking for each terminal: PLUS, MINUS, or BLANK.
12. Set red-ring requirements independently.

Do not use the detected mark to decide which physical terminal is positive or negative.

## 4. Guided validation

Run the required validation captures with known-good batteries. Between accepted samples, vary at least one of:

- X position;
- Y position;
- in-plane rotation;
- normal allowed fixture/line variation;
- representative production lot or battery sample where available.

The wizard does not count a near-duplicate pose. Review:

- match and inlier counts;
- inlier ratio;
- reprojection error;
- scale;
- rotation;
- transformed battery/terminal/marking polygons;
- detected mark/confidence/class scores;
- red-ring decision.

Acceptance: all required varied-pose samples pass, all ROIs visibly follow the battery, and no low-quality crop is labeled BLANK.

## 5. Activate deliberately

- Review the final recipe summary.
- Confirm reference, part number, terminal identities, expected marks, and ring rules.
- Confirm validation count and current configuration fingerprint.
- Select Activate only after quality/engineering approval.
- Verify the previous active revision remains available for rollback.

## 6. Challenge testing

Use controlled samples. At minimum test:

- known-good part;
- reversed top markings;
- red ring on the wrong terminal;
- missing required red ring;
- wrong marking or unexpected symbol;
- genuine blank where BLANK is expected;
- plus/minus where BLANK is expected;
- glare or obstruction that makes the mark unreadable;
- battery near each allowed station edge;
- minimum and maximum allowed rotation;
- partially out-of-frame battery;
- unrelated object/no battery.

Expected behavior:

- known-good: PASS;
- reversed: REJECT - POLARITY MARKINGS REVERSED;
- ring defect: REJECT - RED RING MISMATCH;
- unreadable: REJECT - UNREADABLE MARKING;
- wrong mark: REJECT - TERMINAL MARKING MISMATCH;
- missing/unlocatable battery: REJECT - BATTERY COULD NOT BE LOCATED;
- station/camera failure: SYSTEM FAULT or NOT READY, never PASS.

## 7. Evidence review

For representative pass and every failure type, verify the evidence directory contains:

- original full image;
- capture metadata;
- aligned battery;
- reference battery;
- terminal crops;
- marking crops;
- reference marking crops;
- manifest with recipe revision, registration metrics, class scores, ring results, and final disposition.

Confirm evidence paths and disk retention meet site requirements.

### Optional engineering dataset export

After the known-good validation evidence has been reviewed, export the accepted marking crops for offline analysis:

```powershell
python scripts\export_marking_dataset.py --data-dir runtime --output dataset\markings --clean
```

Review `dataset\markings\summary.json` and `manifest.csv`. Do not add production PASS records with `--include-production-passes` until the active recipe, evidence-retention policy, and quality approval are controlled.

## 8. PLC commissioning

Only after vision qualification:

- verify requested recipe ownership;
- verify one rising edge starts one cycle;
- verify Busy asserts before grading;
- verify Pass/Fail and failure code remain valid until Complete/acknowledgement;
- verify trigger must return false before rearming;
- verify communication loss, timeout, and reconnect behavior;
- verify heartbeat/watchdog;
- verify manual and simulated triggers produce the same evidence and decision path as the PLC trigger.

## 9. Release record

Document:

- application version/commit;
- camera model and firmware;
- lens and working distance;
- lighting configuration;
- camera profile;
- recipe ID/revision/hash;
- validation samples and lots;
- challenge test results;
- false-accept/false-reject acceptance criteria;
- approvers and date.
