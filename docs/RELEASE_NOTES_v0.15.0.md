# Polarity Tool v0.15.0

## Safety correction: terminal-face presence before polarity

A field inspection exposed a gap in the previous pipeline: the marking model correctly received the taught crop, but an open/missing terminal face could still be classified with high confidence because the model was only trained to distinguish marking classes. v0.15.0 adds an independent physical-input gate before polarity classification.

### New inspection order

```text
Battery registration
  -> terminal / marking ROI
  -> terminal-face physical validity
     -> FAIL: REJECT, classifier bypassed
     -> PASS: PLUS / MINUS / BLANK classifier
  -> independent red-ring check
  -> recipe comparison
```

### Terminal-face evidence

The validator compares the current ROI with the same terminal in the accepted known-good recipe reference using low-frequency radial structure, coarse spatial structure, and center appearance. It is designed to ignore fine stamp rotation and ordinary scratches while detecting gross physical changes such as an open hole or missing terminal cap.

Evidence manifests now record terminal-face status, confidence, aggregate score, radial/structure correlations, center appearance deltas, individual gate results, and anomaly count. Diagnostic images include a current/reference comparison and a validity overlay.

### Operator/HMI behavior

- `TERMINAL FACE MISSING` and `TERMINAL FACE INVALID` are product rejects.
- The polarity classifier is not run when the physical gate fails.
- Inspection Detail shows `Terminal face` rather than a misleading direct-crop lock indicator.
- The diagnostic image tab is labeled `INPUT VALIDITY` when face evidence exists.
- PLC fail code 5 is reserved for terminal-face missing/invalid rejects.

### Revalidation

The inspection engine is now `reference_registration_terminal_face_guard_ml_v2` and the manifest/record schema version is 6. Existing recipes must be revalidated under this engine before activation.
