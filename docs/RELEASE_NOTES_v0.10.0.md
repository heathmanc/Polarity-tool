# Polarity Tool v0.10.0 Release Notes

## Purpose

v0.10.0 adds optional ML polarity classification while keeping the existing
OpenCV reference-registration and red-ring inspection architecture. The
production runtime uses ONNX Runtime; PyTorch/Ultralytics are training-only
requirements.

No trained production model is bundled with this release. Until a validated
model package is installed, existing recipes continue to use their currently
validated legacy classifier and new recipe creation clearly reports the active
classifier method.

## ML classifier

A new `onnx_ml` recipe classifier:

- isolates the central metal terminal top before inference;
- never intentionally exposes the red ring or molded case polarity symbol to
  the classifier;
- supports PLUS, MINUS, BLANK, and UNREADABLE;
- optionally averages predictions at four 90-degree rotations;
- applies minimum confidence and class-margin gates;
- applies stricter gates when terminal-top localization falls back to center;
- fails closed on model/runtime errors or image-quality failures.

## Controlled model binding

Station settings contain the model and manifest paths. A new/edited recipe
revision snapshots:

```text
model ID
model version
model SHA-256
ML acceptance thresholds
```

Guided recipe validation includes those values in the configuration
fingerprint. Replacing the ONNX model therefore makes an ML-bound recipe NOT
READY rather than silently changing its inspection behavior.

## Model-package commissioning

**Settings -> VISION / ML** provides:

- ONNX model browse;
- manifest browse;
- Apply & Test;
- model ID/version/hash/classes/input-size display;
- control over whether ML is the default for new/edited recipe revisions.

Apply & Test validates the manifest, file hash, ONNX tensor contract, output
class count, finite output, and ONNX Runtime session before saving the package.

Model changes are blocked only during an active inspection or recipe-validation
cycle, not by unrelated PLC communication state.

## Dataset and training workflow

New utilities:

```text
scripts/export_marking_dataset.py
scripts/add_ml_sample.py
scripts/prepare_ml_dataset.py
scripts/train_marking_classifier.py
scripts/ml_model_probe.py
scripts/evaluate_ml_model.py
```

Automatic dataset export prefers the saved isolated `terminal_top` evidence and
supports PLUS/MINUS/BLANK/UNREADABLE folders. Dataset preparation groups
validation evidence by inspection cycle before splitting train/val/test.

`requirements-training.txt` contains Ultralytics and ONNX training/export
dependencies. `requirements.txt` includes ONNX Runtime for production.

## HMI

The light ISA-101-aligned presentation remains in place. Inspection Detail now
shows the ML model identity, top-class confidence, class margin, and the
terminal-top evidence used by the classifier. Settings add a fixed **VISION /
ML** tab; no scrolling page was introduced.

## Compatibility

- Existing reference-template/geometric recipes remain readable and execute
  with their previous settings.
- Existing validation evidence is not invalidated merely by installing v0.10.0.
- A recipe becomes ML-controlled only in a new/edited revision that is bound to
  an installed model and then guided/validated.
- Persisted station configuration now ignores unknown future top-level/ML keys
  instead of allowing a harmless extra setting to prevent startup.

## Important commissioning boundary

The ML plumbing is production-oriented, but the model itself must be trained and
qualified on representative physical battery data from the site. Do not turn a
proof-of-concept model into a production pass/fail authority without a held-out
challenge set and per-recipe validation.
