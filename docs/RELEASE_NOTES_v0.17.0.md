# Polarity Tool v0.17.0

## Clean commissioning baseline

v0.17 removes the active legacy bench contracts. The first launch archives the previous `runtime` contents to `runtime/archive_pre_v017_<timestamp>` and starts a clean active runtime. Camera and PLC configuration are preserved; recipes, models, training data, validation evidence, and inspection evidence are archived.

The new ML contract is exclusively:

- three classes: PLUS, MINUS, BLANK;
- technician-taught circular terminal-face ROIs;
- masked square model input;
- low confidence/margin reported as NO DECISION rather than a trained UNREADABLE class.

## Validation workflow fix

The Validate page always allows a fresh physical capture once the reference image is accepted. Vision-readiness issues are displayed as blockers but do not disable the capture control. Blocked samples are stored as NOT READY evidence and never count toward recipe validation.

## Dataset preparation fix

PREPARE DATASET uses every eligible circle-contract sample. It no longer quietly excludes legacy images and then reports a surprisingly small dataset. If incompatible records are present, the HMI stops with a clear reset/remove message.
