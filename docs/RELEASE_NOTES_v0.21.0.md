# Polarity Tool v0.21.0

## Release target

v0.21.0 prevents a correctly marked terminal with the wrong visible metal
finish from passing a recipe that requires SILVER or BRASS.

## Recipe contract

- The recipe wizard requires an expected finish for the physical negative and
  positive terminals: `SILVER` or `BRASS`.
- Finish selection is independent of expected PLUS/MINUS/BLANK marking and red
  ring requirement.
- Finish values are included in the recipe validation fingerprint. Changing a
  finish clears earlier validation evidence for that draft.
- A new or edited revision cannot be saved until both primary finishes are
  selected and cannot be activated until guided validation passes.

## Inspection decision

The production order is:

1. terminal face present;
2. visible terminal finish matches the recipe reference;
3. polarity marking is accepted by the existing four-class model;
4. red-ring observation matches the recipe.

A finish mismatch produces `TERMINAL FINISH MISMATCH`. An ambiguous finish or
evaluation error produces `TERMINAL FINISH NO DECISION`. Both are product
rejects and publish only the existing PLC `Fail` BOOL.

## Measurement method

- Uses the exact registered marking-circle crop already isolated for the
  terminal top.
- Compares current and accepted-reference median Lab chroma plus HSV saturation.
- Excludes the darkest engraving pixels and brightest specular highlights.
- Uses directional warmth change to distinguish a wrong brass terminal on a
  silver recipe and a wrong silver terminal on a brass recipe.
- Saves a labeled current/reference comparison and numerical metrics for every
  retained failure and validation sample.

This is an appearance check under the commissioned camera and lighting. It does
not prove alloy chemistry, plating thickness, or supplier identity. Thresholds
must be qualified with representative silver and brass terminals, normal
oxidation, and the full allowed lighting/exposure range before production use.

## Compatibility

- Existing active recipes created before v0.21 load with finish `NOT CONFIGURED`
  and continue using their validated pre-v0.21 behavior.
- Editing one of those recipes requires SILVER/BRASS selection and revalidation.
- Polarity training data and the PLUS/MINUS/BLANK/INVALID_MARKING ONNX contract
  are unchanged; no polarity-model retraining is required for this feature.
- Inspection engine identity remains
  `reference_registration_terminal_face_guard_ml_v2` so untouched legacy recipe
  validation remains valid.
- Manifest schema: `8`.
- Inspection-record schema: `8`.
- PLC contract remains binary `Pass` / `Fail` with no failure-reason tag.
