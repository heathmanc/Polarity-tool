# Polarity Tool v0.20.1

## Release target

v0.20.1 adds an operator-controlled reset for the session-only production yield
counters while preserving the v0.20.0 four-class marking contract.

## Overview counter reset

- **RESET PRODUCTION COUNTERS** appears on Overview.
- It is enabled after at least one production result and disabled while the
  station is busy.
- A confirmation dialog lists the affected and preserved data.
- The reset clears Part, Pass, Fail, reject rate, and the recent-result strip.
- It does not clear the last displayed inspection or delete retained failure
  evidence, recipes, models, training data, or validation records.
- Counters remain session-only and automatically start at zero after restart.

## ML model and recipe behavior

- Completing a training run does not install the candidate and does not affect
  the active recipe.
- Installing a candidate changes the station model.
- Existing recipes retain their previous model ID/version/SHA-256 binding and
  become NOT READY against the newly installed model.
- Create or edit a recipe revision and complete guided validation with the exact
  installed model before returning that recipe to production.

## Compatibility

- Application version: `0.20.1`
- Manifest schema: `7`
- Inspection-record schema: `7`
- Inspection engine remains
  `reference_registration_terminal_face_guard_ml_v2`.
- PLC, numbered-recipe, storage, invalid-marking, and validation contracts are
  unchanged.
