# Polarity Tool v0.11.0 Release Notes

## Guided ML training inside the HMI

v0.11.0 adds a dedicated **ML TRAINING** page to the light ISA-101-aligned HMI. The workflow no longer requires a technician to export or browse inspection evidence folders before model training.

The fixed five-step wizard provides:

1. fresh physical-camera capture;
2. visible, adjustable terminal-top ROI and live crop preview;
3. explicit PLUS/MINUS/BLANK/UNREADABLE labeling;
4. dataset coverage review and duplicate protection;
5. leakage-safe train/validation/test preparation;
6. training-runtime and CUDA detection;
7. in-application Ultralytics classification training;
8. ONNX export and held-out evaluation;
9. gated candidate installation into the station model store.

Training samples are stored beneath `runtime/ml_training`, separate from production inspection evidence. Multiple terminal crops from one fresh frame retain the same capture-group identity and are never split across train/validation/test.

The training worker blocks production inspection execution while it is active but keeps the Qt HMI responsive. Recipe validation remains a separate mandatory step after a candidate is installed.

No production model is bundled with the release.
