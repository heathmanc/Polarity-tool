# Commissioning Polarity Tool v0.8.1

## 1. Verify the release

From the activated virtual environment:

```powershell
python -c "from battery_inspector import __version__; print(__version__)"
python -c "from battery_inspector.build_info import software_build_info; print(software_build_info())"
```

Expected application version:

```text
0.8.1
```

The Git commit should be a 12-character hexadecimal revision in a Git checkout or in the official source archive.

## 2. Run non-GUI regressions

```powershell
python scripts\vision_smoke_test.py
python scripts\stamp_rotation_smoke_test.py
python scripts\terminal_top_gate_smoke_test.py
```

The final script must report:

```text
NEGATIVE ... gate=NOMINAL ... result=PASS
POSITIVE ... gate=CONDITIONAL ... result=PASS
Overall smoke-test status: PASS
```

## 3. Revalidate recipes

v0.8.1 has a new inspection-engine identity. Existing reference images and ROIs remain available, but production activation requires new validation evidence.

For each recipe:

1. Open **Recipes** and choose **Edit / New Revision**.
2. Capture a new known-good reference or deliberately retain the approved reference.
3. Confirm the battery outline, orientation reference, terminal search ROIs, and marking ROIs.
4. Capture all required validation samples with varied battery positions and rotations.
5. Where the terminal head can rotate independently, include varied stamp angles.
6. Confirm that normal well-centered tops show `NOMINAL`.
7. Confirm that a legitimate slightly off-center top may show `CONDITIONAL ACCEPT` only with strong PLUS/MINUS geometry.
8. Activate only after every required sample passes.

## 4. Challenge the conditional gate

Use controlled test parts or engineering fixtures to confirm fail-closed behavior:

- weak or damaged stamp;
- glare or poor focus;
- terminal top partially outside the marking ROI;
- blank terminal;
- scratch resembling one line;
- wrong polarity stamp;
- red ring on the wrong physical terminal;
- marking ROI deliberately moved too far from the terminal.

Expected behavior:

- BLANK never uses conditional top acceptance;
- `CENTER_FALLBACK` never uses conditional acceptance;
- ambiguous/weak geometry is UNREADABLE or falls back conservatively;
- a strong conflicting taught template prevents acceptance;
- wrong polarity remains a product reject.

## 5. Review evidence

For a conditional result, export the cycle ZIP from Inspection Detail and verify the manifest contains:

```text
terminal_top_acceptance: CONDITIONAL
terminal_top_gate_reason: STRONG_CENTERED_STAMP_GEOMETRY
terminal_top_detection_method: HOUGH_CIRCLE
geometry_marking: plus or minus
geometry_confidence >= conditional gate
terminal_top_inside_fraction >= configured gate
```

The stamp overlay must show the selected circle on the actual central terminal top and the measured lines through the stamped feature.
