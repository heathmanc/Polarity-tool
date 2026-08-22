# v0.5.0 — PLC Commissioning and Independent Camera Configuration

## Reported issues addressed

- A physical PLC fault no longer prevents camera commissioning.
- PLC Simulation and a one-shot simulated trigger are now visible directly on the Overview page.
- A failed pycomm3 connection can automatically use a clearly labeled simulation fallback.
- Camera Apply & Test no longer rejects a request merely because the overall station or PLC reports a fault.
- Camera settings requested while startup or an inspection owns the camera are queued and applied when the camera becomes idle.
- Overlapping worker operations use named activity tokens so the header cannot remain stuck on BUSY/INSPECTING when one operation has already completed.
- The recipe-validation table now updates owned cells in place, removing the repeated `QTableWidget: cannot insert an item that is already owned` console warnings.
- Camera and PLC settings merge only their own fields, preventing concurrent commissioning actions from overwriting each other.

## HMI changes

- Added an Overview-page PLC commissioning strip.
- Added a top-of-page PLC simulator panel in Settings.
- Added live Trigger, Busy, Complete, result, fail-code, and recipe feedback.
- Added **ENABLE PLC SIMULATION NOW**, **MAKE SIMULATION PERSISTENT**, and **SEND ONE TEST TRIGGER** actions.
- Separated camera and PLC action-button interlocks.

## Configuration

New field:

```json
{
  "plc_fallback_to_simulation": true
}
```

Existing configuration files inherit `true` unless the field is explicitly set to `false`.

## Validation performed in the build environment

- Python compilation checks
- 35 non-GUI automated tests
- Git repository and release-archive verification

Physical pypylon acquisition, PySide6 rendering, and pycomm3 communication still require target-station testing.
