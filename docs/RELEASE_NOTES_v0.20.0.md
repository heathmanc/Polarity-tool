# Polarity Tool v0.20.0

## Release target

v0.20.0 introduces a learned invalid-marking category without enabling the
proposed geometry veto.

## New class contract

New guided training and new recipe revisions use exactly:

- `PLUS`
- `MINUS`
- `BLANK`
- `INVALID_MARKING`

`INVALID_MARKING` is for a physically present terminal face with a visible
pattern that is not an acceptable PLUS, MINUS, or BLANK. Examples can include
damaged, partial, malformed, doubled, or unrelated markings.

## Fail-closed behavior

- A confident `INVALID_MARKING` inference always returns product REJECT with the
  inspection reason `INVALID MARKING`.
- It can never be selected as a recipe's expected marking.
- Low confidence, low margin, or poor image quality remains `NO DECISION`.
- A missing or grossly invalid terminal face remains `TERMINAL FACE MISSING` or
  `TERMINAL FACE INVALID`; the model is bypassed in that case.
- Red-ring inspection remains independent.

## Guided workflow

- Capture and Review expose `INVALID MARKING` as a fourth label.
- Dataset counts, grouped splitting, training, ONNX manifests, evaluation,
  confusion matrices, and per-class recall include all four classes.
- Existing compatible PLUS/MINUS/BLANK samples are retained. Upgrading does not
  reset or relabel the training store.
- Advisory collection target: 100 independent captures per class. This is not a
  hard training gate; representative diversity and held-out results matter more
  than the raw count.

## Compatibility

- Existing three-class models remain usable for recipe revisions already bound
  to them, avoiding an unplanned station outage.
- A new or edited recipe revision requires an installed four-class model and
  guided validation against that exact model SHA-256.
- Legacy `UNREADABLE` packages remain readable for existing bound revisions.
  `UNREADABLE` is not offered as a new training label.
- Application version: `0.20.0`
- Manifest schema: `7`
- Inspection-record schema: `7`
- Inspection engine remains
  `reference_registration_terminal_face_guard_ml_v2`.

## Bench validation

1. Capture representative samples for all four classes, with multiple physical
   batteries and capture groups.
2. Confirm PREPARE DATASET reports all four classes in TRAIN and independent
   validation data.
3. Train and inspect the held-out confusion matrix, especially false
   `PLUS`/`MINUS` predictions on INVALID MARKING samples.
4. Install the candidate, create a new recipe revision, and complete guided
   validation.
5. Challenge known-good PLUS/MINUS/BLANK terminals, diverse invalid markings,
   low-confidence images, and missing-terminal conditions separately.
