# Polarity Tool v0.17.1

## PLC settings Save & Apply hotfix

v0.17.1 fixes an unintended dependency between PLC settings and the station ML model configuration.

### Symptom

After a model was installed from **ML TRAINING**, the already-created Settings page could retain its startup model path. Pressing **SAVE & APPLY** after changing PLC mode, connection, heartbeat, or tags then interpreted that stale field as a requested ML change. The PLC operation could succeed but the overall save ended with **ML MODEL FAILED VALIDATION** and a missing default `models/polarity_classifier.onnx` message.

### Correction

- A successful ML candidate installation refreshes the ONNX model path, manifest path, and new-revision policy displayed on the Settings page.
- ML controls have their own technician-edit tracking.
- Global **SAVE & APPLY** adds ML validation only when an ML control was actually edited and its requested value differs from the live configuration.
- PLC-only saves no longer depend on ML package availability.
- ML paths remain locked while a multi-service save is active so edits cannot enter midway through the transaction.

Explicit **APPLY & TEST ML MODEL** behavior is unchanged: a technician-requested model change is still verified before it can be persisted or bound to new recipe revisions.

## Compatibility and inspection status

This hotfix does not change:

- the `reference_registration_terminal_face_guard_ml_v2` inspection engine;
- recipe or validation fingerprints;
- evidence schemas;
- the three-class taught-circle ML input contract;
- dataset preparation;
- camera acquisition;
- PLC trigger/result tags;
- heartbeat or bypass semantics.

Existing v0.17.0 runtime data and validated recipes remain eligible. The v0.17 clean-baseline archive runs only when its existing baseline marker is absent; upgrading to v0.17.1 does not perform another reset.
