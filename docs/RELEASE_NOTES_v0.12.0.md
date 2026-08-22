# Polarity Tool v0.12.0 Release Notes

## Multi-ROI guided ML capture

v0.12.0 changes the ML Training capture step from one ROI-at-a-time to a true capture-batch workflow.

A technician can now capture one fresh full-resolution camera frame and draw multiple independent terminal-top ROIs on that same image. The common two-terminal case starts with two ROIs, and additional ROIs can be added up to the fixed HMI limit without using scroll bars.

Each ROI has its own class assignment:

- `PLUS`
- `MINUS`
- `BLANK`
- `UNREADABLE`

The active ROI is shown as the exact ML crop while every ROI remains visible on the full camera frame. **SAVE ALL ROIS** validates all ROIs first, then saves the entire batch under one source capture ID. This preserves leakage-safe dataset splitting: crops from the same physical camera frame can never be separated across train, validation, and test partitions.

## No red-ring confirmation checkbox

The ML capture workflow no longer presents a red-ring verification checkbox. Red-ring inspection is independent from ML classification and is not a training label or training decision.

The HMI retains a fixed instruction reminding the technician that the ML crop must contain only the metal terminal top and stamp. The red ring, molded case polarity symbol, washer, and outer hardware should remain outside each ROI so the classifier cannot learn an unsafe shortcut.

## Global dataset metadata

ML samples remain global across recipes. v0.12.0 adds an optional **Battery / terminal family** tag to each capture batch. This metadata does not change the class label and is not required by the model; it exists so engineering can confirm that the global dataset covers multiple battery families, suppliers, and terminal styles.

The Review page now reports total samples, independent camera frames, and the number of tagged terminal/battery families in the accumulated dataset.

## Batch undo and persistence

- All new ROIs from one save operation can be undone together with **UNDO LAST CAPTURE BATCH**.
- Identical image bytes are still deduplicated within a class.
- Batch sample records include ROI key, batch index, collection tag, capture ID, frame ID, camera identity, crop geometry, and crop-quality measurements.
- Existing v0.11.0 sample manifests remain readable; the new metadata fields default safely when absent.

## HMI constraints

The release preserves the light ISA-101-aligned visual philosophy and fixed, scrollbar-free primary workflow. No inspection-engine or recipe-validation behavior is changed solely by this HMI/data-collection update.
