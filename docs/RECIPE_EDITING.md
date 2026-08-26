# Recipe Editing and Revision Workflow

## Technician workflow

1. Open **Recipes**.
2. Select the product to change.
3. Select **EDIT / NEW REVISION**, or double-click the recipe row.
4. Step 1 opens on the reference image. Choose **CAPTURE NEW REFERENCE**
   (recommended) or **KEEP EXISTING REFERENCE**. The step shows the existing
   reference, the current frame, and the quality gate, so the decision is made
   with those in view rather than before them.
5. If capturing, review the fresh frame. The same button now reads **RETAKE**;
   use it until the frame is right, then select **USE THIS IMAGE**.
6. Confirm the battery outline and orientation reference.
7. Teach the physical negative and positive terminal search ROIs.
8. Adjust the dashed marking ROI inside each enlarged terminal crop.
9. Select the required `PLUS`, `MINUS`, or `BLANK` marking, visible `SILVER` or
   `BRASS` finish, and red-ring requirement for each physical terminal.
10. Run the required fresh validation samples, moving or rotating the known-good battery between samples.
11. Review the registered polygons, locator metrics, detected finish, detected
    marking, confidence/class scores, and ring result.
12. Save as DRAFT, or deliberately activate only when all validation gates pass.

## Reference policy

Every revision must have an explicitly accepted reference image.

- A new capture includes its frame identity, timestamp, resolution, camera backend/profile, quality measurements, and SHA-256.
- Retake replaces the pending capture before it is accepted.
- Keeping an old reference is an explicit technician choice; it is not assumed automatically.
- Imported recipes never trust an external file path as station evidence and force a fresh station capture.
- Saving copies the accepted reference into the revision-specific runtime directory.
- The reference must be a controlled known-good battery. A bad reference can
  anchor the wrong visible terminal finish or visual marking class and must be
  prevented through the site recipe-approval process.

## Revision policy

Recipes are treated as immutable production records:

- editing revision 2 creates revision 3;
- revision 2 is retained in SQLite;
- the active revision remains active while a draft is being developed;
- every edited revision resets validation to zero;
- any teach or vision-setting change invalidates earlier validation evidence;
- a draft cannot activate until locator, classifier, and real-validation gates pass;
- only one revision is active for the station at a time.

## Coordinate behavior

The technician sees full-image normalized rectangles for the battery and terminal search areas. The persisted terminal search ROI is stored relative to the battery rectangle. The marking ROI is stored relative to its terminal crop.

At runtime, OpenCV registration maps the battery reference into the current frame, creates a perspective-aligned battery image, and transforms the taught terminal/marking polygons back to the original camera image. The operator therefore sees the actual current ROIs even when the battery is translated or rotated.

## Validation behavior in v0.8.0

Validation uses the same fresh camera acquisition, reference locator, marking classifier, red-ring detector, and evidence writer as a production inspection. PLC publication and production counts are bypassed.

A passing sample counts only when its pose is sufficiently different from earlier successful samples. The complete validation history is bound to a SHA-256 configuration fingerprint. Changing the accepted reference, battery/terminal/marking ROIs, expected markings, expected terminal finishes, ring requirements, orientation, locator settings, or classifier settings clears prior validation before save.

## Terminal-finish behavior in v0.21.0

SILVER/BRASS is a recipe property, not an ML marking label. The current
registered terminal-top crop is compared with the same physical terminal in the
accepted reference using robust chroma measurements. Dark stamp grooves and
bright glare are excluded. A confident opposite-material shift or an ambiguous
result fails closed and is retained with a current/reference diagnostic.

An untouched pre-v0.21 active recipe remains compatible with finish shown as
`NOT CONFIGURED`. Opening it for a new revision requires the technician to
select both primary terminal finishes and complete fresh validation before that
revision can activate.


## Terminal-top and stamp-angle behavior in v0.8.0

The marking ROI is a search/analysis region, not an exact angular template. Threaded terminal heads can rotate independently of the battery case. During validation and production, the classifier attempts to locate the central terminal top, measures the actual stamp angle, and canonicalizes the observed geometry before recipe-template confirmation.

The technician should ensure the marking ROI:

- contains the complete central circular top at all allowed positions;
- includes modest margin for local circle detection;
- avoids including so much neighboring case geometry that the central top becomes ambiguous;
- is reviewed through the saved terminal-top and overlay evidence.

Changing classifier settings, taught ROIs, or the material inspection-engine version changes the validation contract and requires new validation before activation.

## Conditional terminal-top behavior in v0.8.1

The marking ROI remains a search area, not an exact requirement that the terminal top be centered in the rectangle. Inspection Detail reports terminal-top use as:

```text
NOMINAL
CONDITIONAL ACCEPT
FALLBACK
```

A low-level technician does not configure the conditional thresholds. They should instead ensure that:

- the terminal top is fully contained in the marking ROI;
- the marking ROI excludes as much unrelated washer/hex/ring geometry as practical;
- the actual `+` or `-` is visible in the enlarged crop;
- validation samples cover normal position and terminal-head rotation variation.

`CONDITIONAL ACCEPT` is not an instruction to leave a poor ROI unchanged. During recipe creation, recenter or enlarge the marking search ROI when practical. The conditional gate exists to prevent a small, valid ROI-centering error from discarding otherwise strong PLUS/MINUS evidence; it does not apply to BLANK or ambiguous stamps.
