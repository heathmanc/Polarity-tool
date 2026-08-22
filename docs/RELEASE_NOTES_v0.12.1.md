# Polarity Tool v0.12.1

## ML capture layout

- Removed the active-ROI crop preview from the ML Training capture page.
- The full camera frame with all labeled ROI overlays is now the only capture preview.
- Rebalanced the right pane to prevent overlap: compact active-ROI label, ROI rows, two-row size controls, capture/save/undo actions, and one compact dataset-total line.
- Red-ring verification remains outside ML Training.

## Advisory collection targets

- PLUS/MINUS/BLANK/UNREADABLE targets remain visible, but no longer block wizard navigation or PREPARE DATASET.
- Removed the previous eight-independent-capture engineering gate.
- Dataset preparation works with available samples, keeps each camera frame in one split, protects TRAIN class coverage, and reserves validation/test capture groups when the data permits.
- Sparse validation/test coverage is reported clearly.
- Training requires four-class TRAIN coverage and at least one leakage-safe validation group.
- Candidate installation is gated by held-out class coverage and measured accuracy/abstention performance, not by a configured acquisition target.

## Compatibility

Existing v0.11/v0.12 ML training manifests remain readable. The inspection engine and recipe-validation engine are unchanged, so this HMI/training-policy update does not invalidate existing recipe validation by itself.
