# Release Notes — v0.6.0

## Milestones delivered

### 1. Real acquisition and cycle control

- Manual, PLC Simulation, and pycomm3 triggers share one cycle function.
- A rising-edge latch prevents a physical trigger that remains high from starting duplicate cycles.
- Every cycle calls `camera.capture()` and requires fresh-frame metadata.
- Startup no longer generates a fake inspection.
- A failed capture produces `SYSTEM FAULT — NO NEW CAMERA FRAME` and no stale image substitution.
- Cycle state is authoritative: ACQUIRING, LOCATING, INSPECTING, SAVING, COMPLETE, NOT READY, or FAULT.
- Full-frame evidence is written before analysis.
- Terminal and marking crops plus a JSON manifest are written when recipe geometry is available.
- NOT READY/fault records do not change production yield counters.

### 2. Real recipe reference workflow

- Recipe creation begins with a camera-reference step.
- Recipe editing explicitly asks Capture New, Keep Existing, or Cancel.
- Captures support review, retake, and Use This Image.
- An explicitly `POOR` exposure/focus result blocks acceptance and requires a retake.
- All geometry screens use the accepted reference.
- References are hash-verified, copied to immutable revision directories, and accompanied by a `reference.json` camera/frame/quality metadata sidecar.
- Edited revisions reset validation and remain DRAFT.
- Fake timed validation and immediate activation are removed.

## Deliberate NOT READY behavior

This release does not contain a validated battery pose locator or polarity classifier. The station therefore reports NOT READY after saving the new evidence and preview crops. It will not pass every battery and it will not claim that an unreadable or unimplemented result is a blank marking.

## Workstation acceptance checklist

1. Launch with a real Basler camera and PLC Simulation.
2. Place object A in view and press Run Manual Inspection.
3. Verify a new cycle ID, frame ID, capture timestamp, and evidence directory appear.
4. Replace/move the object and trigger again.
5. Verify the displayed/saved full image changes and the frame identity increments.
6. Temporarily disconnect/block acquisition and verify the cycle faults without showing the previous image as current.
7. Enable PLC Simulation and send a test trigger; verify it follows the same capture path.
8. Edit a recipe and choose Capture New Reference.
9. Capture, retake, and accept a new image; verify all ROI pages display it.
10. Save the revision and verify the prior active revision remains unchanged.
11. Confirm the new revision reference exists under `runtime/recipes/<id>/revision_NNNN/`.
12. Confirm the inspection result says NOT READY because the locator/classifier are not yet validated.

## Known boundaries

- Reference geometry is still applied as a preview at its taught location; translated/rotated battery registration is the next milestone.
- Polarity recognition is not implemented in production runtime; an ONNX classifier will follow data collection/training.
- External hardware-trigger camera profiles require the actual electrical trigger; Apply & Test cannot synthesize a Line1/Line3/Line4 input.
- The GUI and hardware paths require target-PC verification because PySide6, pypylon hardware, and pycomm3 hardware were unavailable in the build environment.
