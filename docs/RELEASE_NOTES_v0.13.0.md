# Polarity Tool v0.13.0

## Scope

v0.13.0 removes two commissioning bottlenecks in the guided ML workflow:

1. A trained ONNX candidate may now be installed **for guided recipe validation** as soon as the ONNX package/runtime verifies. Held-out accuracy/class coverage remain visible engineering warnings/targets, but they are not a hidden hard install gate.
2. The ML Review step is now a persistent, paginated dataset browser. Technicians can review stored terminal-top images, filter by class/family, correct labels, and remove bad samples before preparing/retraining the model.

This release does **not** allow an unvalidated recipe to become active production. Recipe validation remains the production gate.

## Candidate deployment policy

The previous HMI disabled `INSTALL CANDIDATE MODEL` unless the held-out set included all four classes and met 99.5% accepted-result accuracy plus 90% accuracy-with-abstentions. That made model installation impossible on small commissioning datasets even though installation itself only makes a model available for recipe validation.

v0.13.0 separates the gates:

- **Hard installation gate:** ONNX file + manifest + SHA-256 + tensor contract + ONNX Runtime self-test must pass.
- **Engineering warnings:** held-out coverage, accepted-result accuracy, abstention-aware accuracy, and per-class recall.
- **Production gate:** each recipe must create a new immutable revision bound to the exact model SHA-256 and complete guided physical validation.

If held-out metrics are incomplete or below commissioning targets, the HMI requires an explicit confirmation before installing the candidate for recipe validation.

## Candidate recovery

Exported model artifacts remain recoverable after an HMI restart even when the prior training run failed during post-export ONNX Runtime verification. After repairing the runtime, select **LATEST CANDIDATE** and recheck/install the previously exported model instead of retraining.

## Dataset review / correction

The Review step displays six persistent training samples per page with no scroll bars. Controls include:

- class filter;
- battery/terminal-family filter;
- previous/next pagination;
- stored image preview;
- label correction (`PLUS`, `MINUS`, `BLANK`, `UNREADABLE`);
- individual sample removal.

Changes update the persistent `samples.jsonl` manifest immediately and are used by the next dataset preparation/training run. Relabeling preserves capture-group metadata so leakage-safe train/validation/test grouping remains intact.

## Recipe validation transparency

The guided recipe validation page now explicitly displays:

- bound model ID/version/SHA prefix;
- required ML confidence and class-margin gates;
- stricter center-fallback gates;
- required counted PASS sample count;
- exact per-terminal classification status;
- observed confidence/margin and required thresholds.

The Recipes page also shows the classifier and model binding. If a current station ML model is installed but the selected recipe revision is not bound to it, the edit action changes to **CREATE ML REVISION**.

## Training runtime

The runtime check now includes ONNX Runtime as a required training/verification component and distinguishes NVIDIA hardware presence from PyTorch CUDA availability. An NVIDIA GPU with a CPU-only PyTorch build is reported explicitly rather than simply displaying `CPU`.

`requirements-training.txt` now lists ONNX Runtime explicitly. CUDA-enabled PyTorch should still be installed first on NVIDIA engineering stations so Ultralytics uses that existing torch build.

## Compatibility

Existing v0.12.x training samples and recipes remain readable. Existing active recipe revisions are never silently rebound to a newly installed model.
