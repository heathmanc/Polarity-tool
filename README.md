# Pole Position

![Pole Position application icon](battery_inspector/assets/app_icon.png)

Pole Position is a Windows industrial-vision application that checks battery
terminal polarity before a battery continues through a production station. It
uses a Basler camera, reference-image registration, an ONNX marking classifier,
independent terminal-presence and red-ring checks, recipe-controlled terminal
finish checks, and an Allen-Bradley Logix PLC interface.

This README is the current project and handoff guide for the **v0.31.0** source
baseline. Read it before relying on an older release note: release notes describe
the behavior of their point release and can contain terminology that later
releases replaced.

## Contents

- [Release identity and recorded status](#release-identity-and-recorded-status)
- [Purpose and safety boundary](#purpose-and-safety-boundary)
- [What the system inspects](#what-the-system-inspects)
- [System architecture](#system-architecture)
- [HMI pages and responsibilities](#hmi-pages-and-responsibilities)
- [Station requirements](#station-requirements)
- [Install and commission a station](#install-and-commission-a-station)
- [Operator workflow](#operator-workflow)
- [Recipe setup and validation](#recipe-setup-and-validation)
- [ML data collection, training, and deployment](#ml-data-collection-training-and-deployment)
- [PLC interface and cycle behavior](#plc-interface-and-cycle-behavior)
- [Storage, evidence, and retention](#storage-evidence-and-retention)
- [Backup, restore, and workstation replacement](#backup-restore-and-workstation-replacement)
- [Configuration and data locations](#configuration-and-data-locations)
- [Run from source](#run-from-source)
- [Build the Windows installer](#build-the-windows-installer)
- [Verification and acceptance](#verification-and-acceptance)
- [Troubleshooting](#troubleshooting)
- [Change-control invariants](#change-control-invariants)
- [Known limitations and open production work](#known-limitations-and-open-production-work)
- [Project layout](#project-layout)
- [Documentation index](#documentation-index)
- [Release history](#release-history)
- [Handoff checklist](#handoff-checklist)

## Release identity and recorded status

| Item | Current value |
| --- | --- |
| Product name | Pole Position |
| Application version | `0.31.0` |
| Release tag | `v0.31.0` |
| Tagged commit | `b09c10e416dfd09bfe055f5ec5fefaeb8df0f919` |
| Qualified packaging Python | CPython 3.11 x64 |
| Inspection engine | `reference_registration_terminal_face_guard_ml_v2` |
| Evidence manifest schema | `8` |
| Inspection record schema | `8` |
| Current marking classes | `PLUS`, `MINUS`, `BLANK`, `INVALID_MARKING` |
| Current terminal finishes | `SILVER`, `BRASS` |
| PLC result | Binary `Pass` / `Fail`; no fail code |
| Production PASS storage | Memory only |
| Production non-PASS storage | Retained evidence with age/capacity policy |
| Windows installer | Offline x64 PyInstaller/Inno Setup package |

Recorded project status at this handoff point:

- v0.17.0 was the fully bench-tested known-good inspection baseline.
- v0.18.0 through v0.23.4 add fail-only storage, binary PLC results,
  numbered recipes, the invalid-marking class, counter reset, silver/brass
  checks, Pole Position branding, workstation backup/restore, and the Windows
  installer system.
- v0.24.0 corrects a recipe-editor defect that could clear a saved terminal's
  red-ring requirement, marks the rejecting terminal in red on the operator
  screen, stops station pages compressing into overlap on scaled displays, and
  repairs the Windows build so a CUDA request is not silently replaced by a
  CPU-only PyTorch. See `docs/RELEASE_NOTES_v0.24.0.md`.
- v0.25.0 adds an optional PLC result-acknowledge handshake. The tag is
  blank by default, so an existing station's PLC behaviour is unchanged.
- v0.27.0 changes what makes a recipe validation sample count so a fixed-stop
  fixture can validate, makes the sample count a station setting, puts ML
  Training and Settings behind a maintenance passcode, and stops the mouse
  wheel changing values. See `docs/RELEASE_NOTES_v0.27.0.md`.
- v0.28.0 makes the PLC selector decide the recipe on every trigger instead of
  being checked against an activated one, so a mixed line needs no operator and
  the station can run headless. It also publishes station readiness to the PLC
  and separates Logout from Exit. **Controls engineers must re-read the ICD.**
  See `docs/RELEASE_NOTES_v0.28.0.md`.
- v0.29.0 makes the recipe source a station setting instead of something
  inferred from the tag value, so a blank or faulted selector is refused rather
  than silently falling back to the HMI selection; adds triggered-snapshot
  acquisition; and enforces that one recipe number or name names exactly one
  recipe. **Set Recipe source before the first run after upgrading.** See
  `docs/RELEASE_NOTES_v0.29.0.md`.
- v0.30.0 adds two station-to-station transfers smaller than a workstation
  backup: a checksummed ML model package, and a full recipe package carrying a
  revision's reference image, validation evidence, and bound model. See
  `docs/RELEASE_NOTES_v0.30.0.md`.
- v0.31.0 adds the Failure Review screen: browse retained rejects, hold them
  back from retention, add their crops to ML training under labels a technician
  chooses, export them, and clear them. See `docs/RELEASE_NOTES_v0.31.0.md`.
- v0.26.0 adds a live camera preview on the CAMERA IMAGE tab and exposes white
  balance, black level, and gamma. White balance in particular is an inspection
  setting: the silver/brass check compares colour against the recipe reference,
  so changing it requires recapturing every reference.
  See `docs/RELEASE_NOTES_v0.25.0.md`.
- The v0.24.0 installer build has completed on the Windows build computer.
- **Open at this handoff point:** a reported pass on a part that should have
  rejected has not been reproduced or explained. The recipe-editor defect
  corrected in v0.24.0 accounts for the reported display symptom but has not
  been shown to account for a wrong grade. No station may run production until
  `scripts/diagnose_station.py` reports clean and known-good and known-reject
  parts have been run on the physical fixture.
- The installer self-check verifies packaged software, not the real camera,
  PLC, model performance, or station mechanics. Complete site acceptance is
  still required for every deployed station and trained model.

The repository contains 424 pytest test functions, which parameterization expands
to 458 collected cases, plus four command-line smoke
and installation checks. Their presence is not a substitute for recording the
exact test results from the release environment.

## Purpose and safety boundary

Pole Position is a **quality inspection system**. It is not a safety PLC,
machine-guarding device, emergency-stop system, or safety-rated control. The
PLC owns the line permissive, reject, stop, and bypass logic.

The application is designed to fail closed:

- A product can pass only when a fresh image is acquired and every required
  recipe check is evaluated successfully.
- A missing recipe, missing reference, stale/missing frame, invalid model,
  failed registration, low-confidence marking, terminal mismatch, finish
  mismatch, ring mismatch, or internal inspection fault cannot become PASS.
- Product reasons remain detailed in the HMI and failure evidence, while the
  PLC receives only `Pass` or `Fail`.
- HMI bypass never fabricates PASS. It writes a separate bypass request and
  continues reporting the real result. The PLC decides whether that result is
  enforced.

Any site using this application must qualify the complete camera, lighting,
fixture, model, recipe, PLC logic, monitor, and operator workflow. Source-code
review or a successful installer build does not establish measurement-system
capability.

## What the system inspects

For each enabled physical terminal, the current production decision combines
independent evidence:

```text
fresh camera frame
        |
reference-image registration and battery orientation
        |
physical terminal-face presence/validity
        |
visible finish: SILVER or BRASS versus recipe/reference
        |
isolated terminal-top marking: PLUS / MINUS / BLANK / INVALID MARKING
        |
independent red-ring observation
        |
comparison with the active, validated recipe revision
        |
PASS or non-PASS
```

The checks are intentionally separated:

- **Physical terminal identity** comes from the recipe geometry. The detected
  marking never decides which terminal is physically positive or negative.
- **Terminal-face validity** blocks classification of an open hole, missing
  cap, or grossly wrong object.
- **Terminal finish** compares visible appearance with the accepted reference.
  It distinguishes commissioned silver/brass appearance; it does not prove
  alloy chemistry, plating thickness, or supplier identity.
- **Marking classification** sees only the taught, masked terminal-top circle.
  The model must not see the red ring or molded battery-case symbols because
  those are unsafe shortcuts.
- **`INVALID_MARKING`** means a terminal face is present but the visible pattern
  is not an acceptable PLUS, MINUS, or BLANK. It always rejects.
- **NO DECISION** is an inspection result, not a training label. Poor image
  quality, low confidence, or inadequate class margin fails closed.
- **Red ring** is measured independently with conventional OpenCV color logic.

Typical retained non-PASS reasons include:

- `BATTERY COULD NOT BE LOCATED`
- `TERMINAL FACE MISSING`
- `TERMINAL FACE INVALID`
- `TERMINAL FINISH MISMATCH`
- `TERMINAL FINISH NO DECISION`
- `MARKING NO DECISION`
- `INVALID MARKING`
- `TERMINAL MARKING MISMATCH`
- `POLARITY MARKINGS REVERSED`
- `RED RING MISMATCH`
- `NO NEW CAMERA FRAME`
- `NOT READY` or `SYSTEM FAULT` conditions

## System architecture

```text
PySide6 HMI
    |
AppController + Qt signals + global thread pool
    |
    +-- Camera service
    |      +-- BaslerCameraService (pypylon)
    |      `-- MockCameraService (demo image)
    |
    +-- PLC service
    |      +-- AllenBradleyPlcService (pycomm3)
    |      `-- MockPlcService (explicit simulation mode)
    |
    +-- InspectionPipeline
    |      +-- SIFT/ORB reference registration + RANSAC homography
    |      +-- terminal-face physical-input gate
    |      +-- SILVER/BRASS reference-anchored appearance gate
    |      +-- recipe-selected marking classifier
    |      |      +-- ONNX ML classifier (current production path)
    |      |      +-- reference-template compatibility path
    |      |      `-- geometric engineering path
    |      +-- independent red-ring detector
    |      `-- memory-first result/evidence handling
    |
    +-- SQLite recipe/audit/non-PASS repository
    `-- guided ML training and workstation transfer services
```

Manual inspection, simulated PLC triggering, and physical PLC triggering call
the same controller and inspection pipeline. Blocking camera, PLC, training,
backup, and vision work runs outside the Qt UI thread and returns through Qt
signals.

The authoritative inspection state sequence is:

```text
IDLE -> ACQUIRING -> LOCATING -> INSPECTING -> SAVING
     -> COMPLETE | NOT_READY | FAULT
```

One cycle owns one newly acquired `CameraFrame`. If fresh acquisition fails, an
older displayed image is never substituted for the requested product.

A new station acquires that frame as a **triggered snapshot**: the station
executes a software trigger and the camera exposes on demand, so the frame
belongs to the cycle that asked for it. **Free run** remains selectable under
Settings -> CAMERA I/O -> Acquisition, and is what every station commissioned
before v0.29.0 keeps until a technician changes it; there the camera exposes
continuously, and a cycle drains the queue and discards one frame boundary
before grading the next completed exposure. Frame-rate limiting describes
free-run cadence only and is disabled on the camera whenever the station is
triggering.

### Reference registration

The accepted recipe reference is the coordinate authority. Pole Position:

1. detects stable reference/current features with SIFT, using ORB when needed;
2. estimates a homography with RANSAC;
3. checks matches, inliers, reprojection error, scale, visibility, mirroring,
   perspective, and 180-degree orientation;
4. warps the current battery into reference coordinates;
5. maps the current battery, terminal, and marking polygons back onto the
   original full-resolution camera frame.

Terminal areas are masked from pose features so a `+`, `-`, or red ring cannot
determine battery orientation. Registration failure produces an explicit
reject; the pipeline does not reuse the reference coordinates as a guess.

### Model binding

A recipe revision stores the exact ML model ID, version, SHA-256, input crop
contract, and decision thresholds used during validation. Replacing the active
station model therefore does not silently reinterpret an already validated
recipe.

When **Use for new revisions** is enabled and a verified four-class,
taught-circle package is available, the recipe wizard binds a new revision to
that model. The source retains legacy reference/geometric classifier adapters
for previously validated recipes and for controlled recovery. The production
owner should explicitly verify that every newly commissioned recipe displays
the intended `ML / ONNX` classifier rather than assuming that a model is bound.

## HMI pages and responsibilities

The HMI uses a light, ISA-101-aligned visual style. This is an implementation
philosophy, not a formal claim of site ISA-101 compliance.

| Page | Primary use |
| --- | --- |
| Overview | Normal operation, machine state, the recipe that graded the last part, last frame/result, session counts, manual inspection, PLC simulation test trigger, bypass, counter reset |
| Inspection | One-terminal-at-a-time result evidence, expected/detected marking and finish, face validity, ring status, confidence, diagnostics, failure export |
| Recipes | Create, import/export, revise, validate, select, and review numbered recipes |
| Failures | Review retained rejects, hold them from retention, add their crops to ML training, export them, and clear them |
| ML Train | Capture, review, prepare, train, evaluate, and install a classifier candidate |
| Diagnostics | Camera, PLC, model, vision, and resource status |
| Events | Recipe/configuration/bypass/failure audit trail |
| Settings | General, camera, PLC, model, retention, and backup/restore configuration |
| Logout | Locks the maintenance screens and returns to Overview; it is not a user-authentication logout |
| Exit | Confirms and closes the application |

The design target is 1920 x 1080. The application remains usable down to
1280 x 760, but the exact deployed monitor resolution, Windows scaling, touch
behavior, and viewing distance must be accepted at the station.

### Recommended responsibility split

| Role | Normal responsibility |
| --- | --- |
| Operator | Verify Ready/recipe, observe results, use manual inspection when authorized, reset session counters, escalate faults |
| Maintenance | Camera/lighting/mechanical stability, cable/runtime checks, evidence review, backups, workstation replacement |
| Quality / vision engineering | Dataset policy, labeling, training, held-out evaluation, model approval, recipe setup, validation, challenge testing |
| Controls engineering | Logix tags, trigger/result timing, recipe selector, heartbeat watchdog, bypass/interlock logic, recovery testing |
| Software / release owner | Source control, tests, versioning, dependencies, installer build/signing, release artifacts, documentation |

These are procedural roles only. v0.25.0 does not enforce role-based access in
software. The editable “Current technician” name is audit attribution, not an
authenticated identity.

## Station requirements

### Production workstation

- 64-bit Windows 10 22H2 or Windows 11.
- x64 CPU with adequate cooling for continuous operation.
- Sufficient RAM for a 20 MP image, Qt, OpenCV, training, and model export;
  16 GB is a practical minimum and 32 GB is preferred when training locally.
- USB 3 interface suitable for the Basler camera.
- One production camera per station whenever practical.
- Network access to the Logix controller when physical PLC mode is used.
- 1920 x 1080 display recommended.
- Protected machine-wide data directory and a site-approved backup location.

The original project camera is a Basler `acA5472-17uc` 20 MP USB3 camera. The
application deliberately selects the first available Basler device and does not
bind recipes to a camera model or serial number. Camera model and serial are
shown for maintenance verification only.

### Camera software

The target PC needs the compatible Basler pylon runtime and USB camera driver.
The offline Pole Position installer embeds the official signed pylon Runtime
Redistributable supplied during the build and installs:

- `Cpp_Runtime`
- `USB_Runtime`
- `USB_Camera_Driver`

The shared pylon runtime is not removed when Pole Position is uninstalled.

### PLC

Physical communications use `pycomm3.LogixDriver` with CompactLogix or
ControlLogix. PLC Simulation is available for explicit bench use. A physical
PLC failure never changes the selected mode to Simulation automatically.

### Training hardware

Training works on CPU, but GPU acceleration is strongly preferred for repeated
or larger-model experiments. The default checkpoint is a nano classification
model (`yolo11n-cls.pt`); Engineering can browse to a compatible small or medium
classification checkpoint.

Changing model size does not replace the need for better and more varied data.
Move from nano only when held-out results show a capacity problem and qualify
the new package end to end. Current deployed ONNX inference explicitly uses the
CPU Execution Provider, so an NVIDIA GPU accelerates PyTorch training but does
not accelerate the current production inference path.

## Install and commission a station

### Preferred installation

Use the final Inno Setup executable:

```text
Pole-Position-v0.31.0-Setup-x64.exe
```

Do not hand off only `PolePosition.exe` and its `_internal` folder. That is the
intermediate PyInstaller one-directory application, not the complete installer.

The installer places:

```text
C:\Program Files\Pole Position\          read-only application files
C:\ProgramData\Pole Position\            writable station state
```

It preserves `C:\ProgramData\Pole Position` across upgrades and uninstall.

### Separately controlled model files

The installer intentionally contains no `.onnx`, `.pt`, or `.pth` weights.
Supply these separately when required:

```text
polarity_classifier.onnx                 qualified production model
polarity_classifier.json                 matching manifest
yolo11n-cls.pt                           optional training starting checkpoint
```

The default offline training checkpoint location is:

```text
C:\ProgramData\Pole Position\runtime\models\training\yolo11n-cls.pt
```

### New-station commissioning order

1. Install Pole Position as an administrator.
2. Review
   `C:\ProgramData\Pole Position\PolePosition-install-check.json` and confirm
   `ok` is true.
3. Connect the Basler camera and open **Settings > Camera Device**.
4. Scan physical cameras and confirm the intended camera is device 1.
5. Set the production camera source to **Basler required**. `Auto` is useful for
   commissioning but can visibly fall back to the demo image if hardware is
   unavailable.
6. Apply and test exposure, gain, pixel format, acquisition ROI, timeout, and
   acquisition mode. Confirm the full returned resolution. Leave Acquisition on
   **Triggered snapshot** unless the camera cannot do a FrameStart trigger;
   frame rate applies to free run only.
7. Select **Settings > PLC Mode** and choose either explicit Simulation or
   pycomm3. For pycomm3, enter the Logix route and apply/test the connection.
8. Configure every PLC tag. Set **Recipe source**: with **PLC selector tag**,
   also choose recipe-by-name or recipe-by-number, and confirm the program
   writes that tag before the first trigger -- a tag that names nothing is
   refused, not substituted. Choose **Station selection** only for a bench, a
   simulation, or a single-product station whose program has no selector tag.
9. Install or train the current four-class ONNX/JSON model package. Verify model
   ID, version, SHA-256, classes, crop contract, and runtime state.
10. Create or restore the required recipes. Verify number, name, reference,
    terminal ROIs, marking circles, expected markings, finishes, and ring rules.
11. Complete fresh physical recipe validation in varied poses.
12. Activate exactly one qualified recipe.
13. Challenge one known-good part and controlled rejects for reversed marking,
    wrong/missing ring, invalid marking, missing terminal, wrong finish, poor
    image, and recipe mismatch as applicable.
14. Commission the PLC heartbeat, trigger, result lifetime, bypass behavior,
    reconnect behavior, and line interlock.
15. Export a workstation backup and record the installer, model, recipe, PLC,
    camera, and acceptance identities.

Do not release a station merely because the HMI says Ready. Ready proves the
configured software gates are satisfied; it does not prove acceptable false
accept/false reject performance on the site's population.

## Operator workflow

### Before production

1. Confirm the header shows the recipe that graded the last part, and that it
   is the intended product.
2. Confirm the station is Ready and not in Demo, Simulation, Bypass, Not Ready,
   or Fault unless that condition is deliberate and authorized.
3. Check camera and PLC health.
4. Verify the PLC recipe selection names the physical product on the line. The
   PLC selection decides the recipe; there is nothing to select at the HMI.
5. Confirm lighting, lens cleanliness, camera mount, and battery stop position
   are normal under the site procedure.

### Automatic PLC cycle

The PLC provides the production trigger. Pole Position acquires a new frame,
grades it, shows the exact result, updates session counts for valid product
PASS/REJECT outcomes, and publishes the binary PLC result.

### Manual inspection

**RUN MANUAL INSPECTION** on Overview uses the same camera and grading pipeline.
It updates the session counters when it produces a valid product result, but it
does not publish the PLC Busy/Complete/Pass/Fail handshake. PASS remains
memory-only; a manual non-PASS is retained under the normal failure policy.

### Inspection detail

Use **VIEW DETAILS** to inspect one terminal at a time. The screen can show:

- expected and detected marking;
- classifier confidence and class margin;
- terminal-face status;
- expected and detected finish;
- expected and observed red ring;
- exact marking crop passed to the classifier;
- physical-input and finish diagnostics;
- current/reference and registration metadata.

**OPEN EVIDENCE FOLDER** and **EXPORT INSPECTION ZIP** are available only when
the result has retained evidence. They are normally unavailable for production
PASS because no PASS folder exists by design.

### Reset production counters

**RESET PRODUCTION COUNTERS** requires confirmation and works only while idle.
It clears:

- Part count
- Pass count
- Fail count
- Reject rate
- Recent-result strip

It does not delete the displayed inspection, failure evidence, recipes, models,
training data, validation records, or audit history. Counts are session-only and
also begin at zero after an application restart.

### Bypass

Bypass is an abnormal mode. When authorized, **ENABLE BYPASS** writes and reads
back the configured PLC bypass tag. Pole Position still inspects the product and
publishes the actual result. The PLC must decide whether bypass permits the line
to continue.

Never treat the HMI bypass as safety-rated. PLC logic should condition effective
bypass on a healthy heartbeat watchdog so a stale true bit cannot remain
effective after communication loss.

## Recipe setup and validation

Every recipe family has:

- immutable UUID;
- stable positive integer recipe number;
- unique name;
- part number and description;
- immutable revisions;
- accepted reference image and SHA-256;
- battery outline and orientation authority;
- physical terminal search areas;
- marking circles/legacy ROIs;
- expected PLUS, MINUS, or BLANK per terminal;
- expected SILVER or BRASS finish per primary terminal;
- independent red-ring requirement;
- locator/classifier settings and exact model binding;
- configuration-bound validation records.

The recipe number remains the same across revisions. A new recipe defaults to
the next available number and can be changed during initial creation. It is
locked for later revisions. Duplicate recipe names and numbers are rejected.

### Seven-step recipe wizard

1. **Reference** — capture and accept a fresh known-good physical reference, or
   explicitly keep the prior revision's reference.
2. **Identify** — enter the recipe number, name, part number, and description.
3. **Battery** — define the battery outline and orientation reference.
4. **Terminals** — teach physical negative/positive search areas and complete
   terminal-top marking circles.
5. **Polarity** — set the expected marking, visible finish, and ring requirement
   for each terminal; confirm the intended classifier/model identity.
6. **Validate** — run fresh known-good samples through the exact production
   locator, face gate, finish gate, classifier, ring detector, and evidence
   writer.
7. **Complete** — review all settings and save a Draft or, when every gate has
   passed, Save & Activate.

The default requirement is five counted PASS validations at sufficiently
different battery poses. A duplicate pose is retained as evidence but does not
increase the count. Move and rotate the known-good battery between captures;
also cover allowed terminal-head rotation.

### Revision and validation rules

- Editing creates a new revision; it does not overwrite the active revision.
- The active revision remains available while a new Draft is developed.
- Any changed reference, ROI, expected mark, finish, ring rule, locator setting,
  classifier setting, model identity/hash, or material engine contract resets
  validation.
- Activation requires matching real validation evidence, not only a numeric
  pass count.
- A validation capture remains available when readiness is blocked so the
  technician can collect diagnostic crops. That sample does not count until the
  recipe is ready and the full result passes.
- Recipe import is a template transfer, not workstation migration. An imported
  recipe requires a fresh station reference and validation.
- Use workstation backup/restore when transferring the entire commissioned
  station state.

For detailed screen behavior, see
[`docs/RECIPE_EDITING.md`](docs/RECIPE_EDITING.md).

## ML data collection, training, and deployment

### Why classification is used

The recipe registration and terminal ROIs already locate the physical terminal
and isolate the terminal top. The learned task is therefore “which permitted
marking pattern is in this already located crop,” which is a classification
problem. A detection model would add annotation and runtime complexity without
replacing reference registration, physical identity, finish, or ring checks.

A detector may become justified if measured data shows that the existing
registration/ROI path cannot reliably locate certain battery or terminal
families. That decision should be driven by failure-rate evidence, not by model
size alone.

### Current four-class contract

Every new guided model must contain exactly:

```text
plus
minus
blank
invalid_marking
```

Use `INVALID_MARKING` for a physically present terminal face with a damaged,
partial, malformed, doubled, unrelated, or otherwise unacceptable visible
pattern. Do not use it for:

- a missing terminal or open hole — the physical-input gate owns that case;
- blur, glare, clipping, or low confidence — those are NO DECISION conditions;
- a red-ring problem — the independent ring check owns that case;
- silver/brass identity — finish is a recipe/reference check.

### Capture rules

- Capture only from the fresh physical camera; demo images are not accepted for
  production training.
- Draw the circle around the **complete flat metal terminal face**.
- Keep the red ring, washer, outer hex, and molded case polarity symbol outside
  the circle.
- Label what is actually present, not what the recipe expected.
- Use the optional family tag to measure coverage across battery/terminal
  families. It does not create a recipe-specific model.
- Include different physical parts, suppliers, lots, finishes, engraving depth,
  wear, oxidation, scratches, allowed positions, terminal rotations, and normal
  lighting variation.
- Do not fill a dataset with many near-identical frames. Diversity matters more
  than raw count.

The wizard displays an advisory target of roughly 100+ independent captures per
class. It permits earlier training, but all four classes must appear in training
and the held-out evaluation must be credible. Ten images per class can prove the
workflow, but should not be treated as production qualification.

### Five-step ML Training workflow

1. **Capture** — acquire a full-resolution frame, place one or more marking
   circles, label each circle, and save the batch.
2. **Review** — filter by class/family, inspect samples, correct labels, and
   remove bad crops.
3. **Prepare** — create grouped train/validation/test splits and verify the
   training runtime/device. All crops from one camera frame remain in one split
   to prevent leakage.
4. **Train** — select base checkpoint, epochs, image size, batch, and CPU/CUDA
   device. Training and ONNX export run in a background worker.
5. **Deploy** — review held-out results and install the candidate for recipe
   validation.

The guided data lives below `runtime/ml_training` and is included in workstation
backup.

### Nano, small, or medium model

- Nano is the default because the crop is small and the class count is low.
- Small or medium may improve difficult decision boundaries, but they also take
  longer to train, require more representative data, and can overfit a small
  dataset just as easily.
- Compare candidate models on an independent challenge set, especially false
  PLUS/MINUS predictions on invalid markings and cross-finish/cross-family
  performance.
- Keep preprocessing, crop contract, class order, thresholds, and recipe
  validation identical during the comparison.

### Training versus installation versus recipe validation

These are three separate state changes:

1. Finishing a training run creates a candidate. It does **not** change the
   station model or active recipe.
2. **INSTALL CANDIDATE FOR RECIPE VALIDATION** changes the station model after
   package/runtime verification.
3. A recipe bound to a different model ID/version/SHA becomes NOT READY. Create
   or edit a revision, bind the installed candidate, and complete fresh physical
   validation before production resumes.

Never overwrite an approved model in place. Keep its ONNX and JSON manifest
together and use a new model version. The manifest hash check prevents pairing
the JSON file with different weights.

More detail is in
[`docs/ML_TRAINING_WIZARD.md`](docs/ML_TRAINING_WIZARD.md) and
[`docs/ML_CLASSIFICATION.md`](docs/ML_CLASSIFICATION.md).

## PLC interface and cycle behavior

### Default Logix tags

All tag names are editable under **Settings > PLC Tags**.

| Purpose | Default tag | Suggested type | Direction |
| --- | --- | --- | --- |
| Trigger | `BatteryVision.Trigger` | BOOL | PLC -> HMI |
| Busy | `BatteryVision.Busy` | BOOL | HMI -> PLC |
| Complete | `BatteryVision.Complete` | BOOL | HMI -> PLC |
| Pass | `BatteryVision.Pass` | BOOL | HMI -> PLC |
| Fail | `BatteryVision.Fail` | BOOL | HMI -> PLC |
| Recipe selector | `BatteryVision.RecipeName` | STRING or SINT/INT/DINT | PLC -> HMI |
| Heartbeat | `BatteryVision.Heartbeat` | BOOL | HMI -> PLC |
| Bypass request/read-back | `BatteryVision.Bypass` | BOOL | HMI -> PLC and HMI read-back |

There is no PLC failure-reason tag. Detailed reasons remain in the HMI and
retained evidence.

### Authoritative PLC mode

The combobox under **Settings > PLC Mode** is the only authority:

- `Simulation` uses the internal mock PLC.
- `pycomm3` uses the configured physical Logix route.

There is no automatic PLC simulation fallback. Connection, polling, heartbeat,
or tag failure keeps the physical PLC state faulted until it is corrected or an
authorized person explicitly selects Simulation and applies it.

### Recipe selection

The single configured recipe selector tag can be interpreted as:

- **Recipe name** — Logix STRING; or
- **Recipe number** — Logix SINT, INT, or DINT.

The received value decides the recipe on every trigger: the station resolves it
to the newest revision of that recipe whose validation is complete, and grades
the part against that revision. There is no activation step and no station-side
selection in the PLC path, so a mixed line needs no operator and the station can
run headless. Recipe numbers are stable across revisions.

A recipe that cannot be resolved -- unknown name/number, or only draft or
retired revisions -- is refused and never substituted. The refusal is logged,
Ready goes false while the condition persists, and the trigger is
ignored; the HMI does not start a cycle or publish a synthetic FAIL for that
edge. The PLC must prevent product advance and implement its site-standard
timeout/fault response to a request that does not produce Busy/Complete.

### Trigger and result sequence

The trigger is polled (250 ms default) and accepted on a rising edge. It must be
observed false before another rising edge can be accepted. A level that remains
true does not retrigger continuously. If a PLC edge arrives while the camera is
temporarily occupied, one pending PLC inspection is retained.

For an accepted PLC cycle:

| Phase | Busy | Complete | Pass | Fail |
| --- | ---: | ---: | ---: | ---: |
| Connection/apply idle clear | 0 | 0 | 0 | 0 |
| Inspection running | 1 | 0 | 0 | 0 |
| Completed PASS | 0 | 1 | 1 | 0 |
| Completed non-PASS | 0 | 1 | 0 | 1 |

Exactly one result bit is true whenever `Complete` is published. REJECT,
NOT READY, acquisition failure, and internal fault all map to `Fail` when the
cycle was initiated by the PLC.

**As-built result lifetime:** v0.25.0 has no result sequence number,
acknowledgement tag, or complete-clear-on-trigger-low state. The completed row
remains written after the cycle. It is cleared when the next accepted cycle
writes Busy or when PLC settings are connected/applied and the idle row is
verified. PLC logic must consume `Complete` as the validity of the latest result
and must not wait for the HMI to clear it after Trigger falls.

### Heartbeat

The HMI toggles the heartbeat independently of inspection activity (1000 ms
default). The PLC should detect transitions rather than treating true as
healthy. At the default interval, a 3000-4000 ms watchdog is recommended.

```text
IF BatteryVision.Heartbeat <> Last_HMI_Heartbeat THEN
    Last_HMI_Heartbeat := BatteryVision.Heartbeat;
    Reset_HMI_Watchdog := TRUE;
ELSE
    Reset_HMI_Watchdog := FALSE;
END_IF;
```

Use the site's standard timer and fault handling. A failed HMI heartbeat write
stops polling and reports a PLC fault; it does not select Simulation.

### Bypass logic concept

```text
HMI_Comm_OK := heartbeat watchdog healthy;
Bypass_Effective := BatteryVision.Bypass AND HMI_Comm_OK;
Inspection_Interlock_OK := Bypass_Effective OR BatteryVision.Pass;
```

This is a concept, not safety logic. Adapt it to the approved PLC programming
standard, interlocks, reject mechanics, and failure recovery.

The complete PLC specification and commissioning checklist are in
[`docs/PLC_INTERFACE.md`](docs/PLC_INTERFACE.md).

## Storage, evidence, and retention

### Memory-first production behavior

| Outcome | Image/crops in RAM | Evidence folder | SQLite inspection row | Per-cycle audit event |
| --- | --- | --- | --- | --- |
| Production PASS | Until next result/exit | No | No | No |
| Production REJECT | During grading/display | Yes | Yes | Yes |
| Production NOT READY / fault | During grading/display | Yes when materialization succeeds | Yes | Yes |
| Recipe validation PASS/non-PASS | During grading/display | Yes | Validation record/evidence | Yes |

The absence of PASS history is intentional. Do not add a hidden PASS image,
manifest, database row, audit entry, or persistent aggregate counter without a
new approved storage requirement.

Session counters are not traceability records. They reset on startup and can be
reset from Overview while idle.

### Failure retention

Default settings under **Settings > General**:

| Limit | Default | `0` behavior |
| --- | ---: | --- |
| Failure age | 30 days | Age limit disabled |
| Failure capacity | 5.0 GB | Capacity limit disabled |

When both are enabled, the oldest positively identified non-PASS evidence is
removed when either limit requires it. The newest failure package is preserved
even when it alone exceeds the configured capacity. Unknown/incomplete folders
are left for manual review rather than deleted blindly.

Retention traverses only:

```text
runtime/inspections/YYYYMMDD/<cycle-id>/
```

It does not delete recipe references, validation templates, validation
captures, ML samples, datasets, training runs, installed models, configuration,
or audit records.

A failure marked **KEEP** on the Failures page is held back from both the age
and capacity passes until it is released. The failure worth investigating is
usually the one somebody is still working on, and it was also the one most
likely to age out of the window before they got to it. KEEP never applies to
PASS evidence: production PASS is memory-only and is removed unconditionally.

### Reviewing failures

**Failures** lists every retained non-PASS record newest first, with filters for
triage state, age, and reason text. Opening one renders it in the same detail
view the operator saw live. Each record carries a triage state -- NEW, REVIEWED,
SENT TO TRAINING -- with who moved it and when.

Four actions operate on the selection:

| Action | What it does |
| --- | --- |
| KEEP / RELEASE | Holds the record back from retention, or lets it age out |
| ADD TO ML TRAINING | Adds the part's terminal crops to the training set under labels the technician chooses |
| EXPORT SELECTED | Writes the records and their evidence as one checksummed ZIP with a summary index |
| CLEAR SELECTED | Deletes the evidence and the rows, after a confirmation naming what is held or never exported |

**The technician labels the crop, never the model.** A rejected part is exactly
the case where the classifier may have been wrong, so the label dialog
preselects nothing and shows the station's reading only as context. Defaulting
to the detected class would train the model on its own mistakes. Crops are taken
from the stored full-resolution frame using the recorded terminal outline and
re-cropped through the same `ml_input_crop` contract a live capture uses, so a
sample added here is indistinguishable from one captured on the ML Training
page.

Clearing is scoped by the same rule as retention: only a two-level cycle
directory beneath `runtime/inspections/` carrying a readable manifest can be
removed, whatever path is passed. It never acts on the whole list implicitly --
an empty selection is a no-op.

### Typical retained evidence

Depending on where the cycle failed, a non-PASS directory can include:

```text
full.jpg
capture.json
aligned_battery.jpg
reference_battery.jpg
<terminal>_terminal.png
<terminal>_marking.png
<terminal>_reference_marking.png
<terminal>_ml_input.png
<terminal>_input_validity.png
<terminal>_finish_comparison.png
other diagnostic overlays
manifest.json
```

The manifest includes cycle/frame identity, build/engine/schema identity,
camera profile, active recipe/revision/reference hash, registration metrics,
transformed polygons, physical gate, finish, classifier/model scores and
thresholds, ring measurements, and final disposition/reason.

See [`docs/STORAGE_POLICY.md`](docs/STORAGE_POLICY.md).

## Backup, restore, and workstation replacement

Use **Settings > General > Export Workstation Backup** before a workstation
replacement, major service, model change, or controlled software upgrade.

The ZIP includes:

- `config.json`;
- a consistent SQLite snapshot with recipes and audit history;
- immutable recipe references and validation templates;
- validation evidence present in the runtime;
- ML samples, datasets/runs, installed models, and externally configured model
  files copied into the portable package;
- retained non-PASS evidence;
- a manifest with application version, original roots, file sizes, and SHA-256
  for every member.

It does not include production PASS history, Python, Pole Position application
files, the Basler runtime/driver, or the Windows installer.

### Restore procedure

1. Install the same or approved newer Pole Position release on the replacement
   PC.
2. Launch once and confirm the installation check.
3. Open **Settings > General > Import Workstation Backup**.
4. Select the untouched Pole Position backup ZIP.
5. The application verifies paths, encryption state, schema, counts, sizes,
   hashes, JSON, and SQLite integrity before staging anything.
6. Close/restart when prompted. Restore is applied before camera, PLC, model,
   and database services start.
7. Pole Position preserves the destination PC's selected data-directory
   location and rebases stored absolute paths.
8. A complete pre-restore rollback ZIP is created under `restore_rollback`.
9. Recommission camera, PLC tags/heartbeat/result, model, active recipe, known
   PASS, and known reject before production.

Do not edit the backup ZIP. The archive is checksummed but not encrypted; store
and transfer it under the site's data-protection policy.

See [`docs/BACKUP_RESTORE.md`](docs/BACKUP_RESTORE.md).

## Configuration and data locations

### Installed application

```text
C:\Program Files\Pole Position\
    PolePosition.exe
    _internal\
    BUILD-MANIFEST.json
    THIRD_PARTY_PACKAGES.txt

C:\ProgramData\Pole Position\
    config.json
    PolePosition-install-check.json
    models\
    runtime\
        battery_inspector.db
        recipes\
        validation\
        inspections\
        ml_training\
        models\
        recipe_staging\
```

### Source checkout

Source launches keep mutable state in the repository by default:

```text
<source-root>\config.json
<source-root>\runtime\
```

Both are ignored by Git. Do not commit station data or production models by
accident.

### Important configuration defaults

| Setting | Default |
| --- | --- |
| Camera source | `auto` — first Basler, labeled demo fallback if unavailable |
| PLC mode | `simulation` |
| PLC route | `192.168.1.10/1` |
| PLC poll | 250 ms |
| Heartbeat | 1000 ms |
| Recipe selector | Name |
| Failure retention | 30 days / 5.0 GB |
| Full screen | Off |
| Current technician | `Technician` |

The example is [`config.example.json`](config.example.json). Unknown persisted
keys are ignored for forward/backward configuration tolerance. Safety-relevant
recipe decisions live in versioned recipe payloads rather than being silently
reinterpreted from station settings.

### Environment overrides

These are intended for controlled deployment, test, or packaging use:

| Variable | Effect |
| --- | --- |
| `POLE_POSITION_HOME` | Overrides the writable station root |
| `BATTERY_INSPECTOR_DATA_DIR` | Overrides the runtime data directory |
| `POLE_POSITION_RESOURCE_DIR` | Overrides read-only resources; primarily packaging verification |
| `POLARITY_TOOL_GIT_COMMIT` | Injects build revision into evidence metadata |

Document any override in the station handoff record. An undocumented path
override can make a backup appear incomplete or make a replacement station use
the wrong runtime.

## Run from source

Python 3.11 x64 is the qualified baseline. The project metadata allows newer
Python versions for development, but release packaging is intentionally pinned
to 3.11 x64.

### Full station and training environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py
```

`requirements.txt` includes HMI, OpenCV, ONNX Runtime, pypylon, pycomm3,
PyTorch, Ultralytics, and ONNX export support. Install the matching Basler pylon
runtime separately when running from source.

For a CPU/demo-only development environment:

```powershell
python -m pip install -r requirements-demo.txt
python run.py
```

For editable development with tests and linting:

```powershell
python -m pip install -e ".[dev,hardware,training]"
python -m pytest
python -m ruff check battery_inspector scripts tests
```

Press `F11` to toggle full screen.

### Camera probe

```powershell
python scripts\camera_probe.py --grab
```

Run this before debugging the HMI when pylon import, camera enumeration, or a
full-resolution grab is suspect.

### Engineering ML utilities

The HMI wizard is the normal workflow. Command-line tools remain available for
engineering recovery and automation:

```powershell
python scripts\export_marking_dataset.py --data-dir runtime --output dataset\markings --clean
python scripts\prepare_ml_dataset.py --clean
python scripts\train_marking_classifier.py --device cpu
python scripts\evaluate_ml_model.py --data dataset\polarity_cls\test
python scripts\ml_model_probe.py --model models\polarity_classifier.onnx --manifest models\polarity_classifier.json
```

Use each script's `--help` for the exact current arguments before building an
automated process around it.

## Build the Windows installer

The build must run on 64-bit Windows with:

1. Windows 10 22H2 or Windows 11;
2. Python 3.11 x64;
3. Inno Setup 6;
4. the official signed Basler pylon Runtime Redistributable executable;
5. internet access for dependency resolution on the build computer.

The target station does not need Python, pip, Inno Setup, Visual Studio, or
internet access.

To produce a fully bundled application **without** Inno Setup and without the
pylon Runtime Redistributable — for a development machine or a commissioning
bench — use the local build instead:

```cmd
BUILD_WINDOWS_APP.cmd -Clean
```

It runs the same PyInstaller spec and keeps the same guards, and it writes
`dist\windows-local\Pole-Position-v<version>-win64\PolePosition.exe`. A station
still receives the installer built below; see
[Windows installer](docs/WINDOWS_INSTALLER.md) for the switches and for what
remains unbundled.

### Standard CPU build

Open PowerShell in the source root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\build-installer.ps1 `
  -PylonRuntime "C:\Installers\pylon_Runtime_x64.exe" `
  -Clean
```

The script defaults to `python`, so an activated Python 3.11 venv is honored.
An explicit interpreter is also valid:

```powershell
.\packaging\windows\build-installer.ps1 `
  -PylonRuntime "C:\Installers\pylon_Runtime_x64.exe" `
  -PythonCommand "C:\yolo\.venv\Scripts\python.exe" `
  -Clean
```

The script uses that interpreter to verify Python 3.11 x64 and create its own
controlled build environment under `build\windows\.venv`.

The root wrapper performs the standard CPU build:

```cmd
BUILD_WINDOWS_INSTALLER.cmd "C:\Installers\pylon_Runtime_x64.exe"
```

### CUDA-enabled training build

Choose the PyTorch wheel index that matches the approved NVIDIA driver/toolkit:

```powershell
.\packaging\windows\build-installer.ps1 `
  -PylonRuntime "C:\Installers\pylon_Runtime_x64.exe" `
  -TorchIndexUrl "https://download.pytorch.org/whl/cu128" `
  -Clean
```

This changes the packaged PyTorch training runtime. Current ONNX production
inference remains CPU-only.

### Reproducible and signed builds

The first controlled build writes a complete dependency lock. Supply it to a
later build with `-RequirementsLock`. Continue using the same PyTorch index when
the lock depends on a CPU- or CUDA-specific wheel source.

For production distribution, sign both the frozen executable and installer:

```powershell
.\packaging\windows\build-installer.ps1 `
  -PylonRuntime "C:\Installers\pylon_Runtime_x64.exe" `
  -SignCertificateThumbprint "CERTIFICATE_THUMBPRINT" `
  -Clean
```

Unsigned bench builds are permitted by the script but may show Unknown
Publisher or SmartScreen warnings.

### Final outputs

Use the files under `dist\windows`, not the intermediate frozen directory:

```text
dist\windows\Pole-Position-v0.31.0-Setup-x64.exe
dist\windows\Pole-Position-v0.31.0-Setup-x64.exe.sha256
dist\windows\Pole-Position-v0.31.0-requirements-lock.txt
```

The build:

- verifies Python and the Basler Authenticode signature;
- installs/checks all dependencies;
- runs the source installation check;
- freezes the full HMI and training stack;
- excludes ONNX/ONNX Runtime test datasets;
- fails if any `.onnx`, `.pt`, or `.pth` remains in the frozen application;
- records package, source, pylon signer, and hash information;
- runs the frozen `--verify-install` self-check;
- compiles the Inno Setup installer with absolute input/output paths;
- writes the installer SHA-256.

See [`docs/WINDOWS_INSTALLER.md`](docs/WINDOWS_INSTALLER.md).

## Verification and acceptance

### Development regression commands

```powershell
python -m compileall -q battery_inspector scripts tests
python -m pytest
python scripts\verify_install.py
python scripts\vision_smoke_test.py
python scripts\stamp_rotation_smoke_test.py
python scripts\terminal_top_gate_smoke_test.py
python -m ruff check battery_inspector scripts tests
```

`scripts\verify_install.py` checks the source dependencies and the bundled demo
pipeline. `PolePosition.exe --verify-install` is a different noninteractive
frozen-package check used by the installer.

The CI definition in [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
runs pytest and the three vision smoke tests on Python 3.11 and 3.12, on both
Windows and Linux, because the station is a Windows x64 application. Ruff and
the source-integrity check run on Linux only: lint results are
platform-independent, and the checksum manifest records committed bytes, which
a Windows checkout rewrites to CRLF line endings.

CI does not build the Windows installer. `build-installer.ps1` requires the
licensed Basler pylon Runtime Redistributable and verifies its Authenticode
signature, and that redistributable cannot be published to a hosted runner. The
installer therefore remains a controlled local build following
[`docs/WINDOWS_INSTALLER.md`](docs/WINDOWS_INSTALLER.md); CI verifies the
application and that every build input the spec and installer reference is
present.

### Source integrity

`SHA256SUMS.txt` covers tracked source files except itself and the Git
archive-substituted `_git_archival.txt`. On a system with GNU coreutils:

```bash
sha256sum -c SHA256SUMS.txt
```

Regenerate the manifest whenever tracked release content changes:

```bash
python scripts/verify_source_checksums.py --write
```

The same script without `--write` verifies the manifest and is the check CI
runs. It reports changed digests, tracked files missing from the manifest, and
recorded files that are no longer tracked. A successful checksum validates
bytes against that manifest; it does not establish that the manifest came from
a trusted signer.

### Minimum station FAT/SAT

Record pass/fail evidence for at least:

- cold boot and normal shutdown;
- installer self-check;
- camera power cycle, USB reconnect, and full-resolution grab;
- camera exposure/gain/ROI apply-and-test;
- physical PLC connect, tag read/write, rising-edge trigger, result, and
  heartbeat watchdog;
- recipe name/number match and mismatch;
- bypass on/off/read-back and heartbeat-gated effective bypass;
- active recipe/model/reference/validation readiness;
- known-good PASS;
- reversed PLUS/MINUS;
- wrong or missing red ring;
- invalid marking;
- marking no-decision condition;
- missing/invalid terminal face;
- wrong silver/brass appearance;
- allowed battery displacement/rotation and terminal-head rotation;
- camera/PLC/model failure recovery;
- fail evidence creation/export and retention;
- workstation backup, restore on a spare/staged PC, and rollback creation;
- session counter reset;
- exact monitor resolution/scaling and touch/keyboard operation.

For model qualification, split challenge material by physical part, lot, date,
or supplier—not by near-duplicate frame—and record false accept, false reject,
no-decision, and localization failure rates against approved limits.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| HMI is `NOT READY` | Active recipe, accepted reference file, required real validation count, configuration fingerprint, classifier/model binding, model runtime, locator readiness |
| Model will not apply | ONNX and JSON are a matching pair; SHA-256 matches; classes are exactly the current four; crop contract is `taught_circle_masked_square_v1`; ONNX self-test and tensor shapes pass |
| Recipe stopped after installing a model | Expected behavior when its bound model hash differs. Create/edit a revision, bind the candidate, and validate it. Merely completing training does not change the recipe; installing does |
| Physical camera not available | pylon runtime/driver, USB3 cable/port/power, camera enumeration, first-device selection, `camera_probe.py --grab`, and Camera source mode |
| HMI shows Demo fallback | Camera source is Auto and no usable Basler device opened. Correct hardware and apply/test, or select Basler required for production fail-closed behavior |
| PLC stays faulted | Confirm pycomm3 mode, Logix route, network, tag names/types, output BOOL writability, selector type, bypass tag, and heartbeat write. It will not fall back automatically |
| PLC trigger does nothing | Trigger needs a new rising edge; the requested recipe must resolve to a validated revision (check Ready); PLC polling must be healthy; camera may have one queued PLC cycle |
| PLC waits forever for Complete to clear | v0.25.0 latches the completed result until the next Busy or reconnect/apply, unless the optional acknowledge tag is configured. There is no clear-on-trigger-low state |
| Complete arrives with Fail | Inspect HMI detail/evidence. Every non-PASS PLC cycle maps to Fail; there is no reason code |
| PASS has no evidence folder/export | Expected memory-only PASS policy |
| Counter reset did not remove evidence | Expected; it clears only session totals/recent strip |
| Training uses CPU | Check ML Training runtime/device list and the installed CUDA-enabled PyTorch build. ONNX production inference remains CPU-only |
| Prepared dataset is unexpectedly small | Review labels, missing image files, duplicate bytes, same-frame grouping, class filters, and the four-class contract |
| Restore does not happen immediately | Import stages the archive; restart Pole Position to apply it before services open |
| Installer build produces only `PolePosition.exe` + `_internal` | That is the PyInstaller intermediate. A successful build continues through Inno Setup and writes the Setup EXE under `dist\windows` |
| Inno Setup not found | Install Inno Setup 6 or pass the full `-InnoCompiler` path. The script checks Program Files and the standard per-user LocalAppData path |
| Windows warns about publisher | The build was not Authenticode-signed. Use a controlled certificate for production distribution |

For diagnosis, retain the exact error text, build version/commit, station backup,
active recipe/model identities, camera description, PLC route/tag map, and one
exported failure package when available.

## Change-control invariants

Preserve these behaviors unless a new approved requirement explicitly replaces
them:

1. One fresh camera frame per cycle; never grade a cached frame after an
   acquisition failure.
2. Manual, simulated PLC, and physical PLC paths use the same inspection
   pipeline.
3. Product PASS requires complete evaluated evidence and fails closed on
   uncertainty.
4. Production PASS remains memory-only.
5. Production non-PASS remains evidence-backed and retention-bounded.
6. Validation evidence, recipe references, models, and ML training data remain
   outside failure-retention deletion scope.
7. Recipe edits create immutable revisions and reset validation when the
   decision contract changes.
8. An ML recipe is bound to exact model ID/version/SHA and crop contract.
9. The marking model never receives red-ring or molded case-symbol context.
10. Physical terminal identity, terminal presence, finish, marking, and ring
    remain independent checks.
11. PLC result remains mutually exclusive binary Pass/Fail unless the controls
    contract is deliberately versioned.
12. PLC mode never falls back to Simulation automatically.
13. Recipe number remains stable across revisions and can be selected through
    an integer Logix tag.
13a. The PLC selector decides the recipe on every trigger when the station's
    recipe source is the PLC. The station resolves it to the newest validated
    revision of the named recipe and grades against that; an unresolvable or
    empty selection is refused, never substituted, and drops Ready. Where a
    trigger's recipe comes from is always a station setting, never inferred
    from the value read.
13b. A recipe number and a recipe name each identify exactly one recipe. Both
    are how the PLC names a product, so a duplicate is refused at save and
    reported by the station diagnostic.
13c. Only the station decides when the camera exposes. Hardware triggering is
    normalized away; acquisition is either a station-issued software trigger or
    free run sampled by the station.
13d. Retained failure evidence is reviewable, and what is done with it is
    recorded: triage state, who reviewed it, what was exported, what went to
    training. A crop added to the training set is labelled by a technician,
    never by the classifier that produced the reject.
14. Bypass never manufactures PASS and must be owned by PLC interlock logic.
15. Camera selection remains first available and serial-independent.
16. Production model weights remain separate from the Windows installer.
17. Installer upgrades/uninstall preserve machine state under ProgramData.

For a behavior change, update code, tests, `BUILD_NOTES.md`, the appropriate
release note, this README, version declarations, and `SHA256SUMS.txt`. Re-run
the complete regression and station acceptance scope affected by the change.

## Known limitations and open production work

The following are current as-built boundaries, not hidden completed features:

- **No role-based authentication or authorization.** The technician name is an
  editable attribution string. Logout locks the ML Training and Settings
  screens behind the maintenance passcode and returns to Overview; Exit closes
  the program.
- **No PLC sequence/acknowledgement transaction.** The current interface is a
  rising-edge trigger with latched latest result as documented above.
- **An unresolvable recipe selection is refused/logged and drops Ready, not
  converted into a completed Fail transaction.** PLC timeout/permissive logic
  must handle it.
- **Lighting is not measured.** The station has no lighting measurement path,
  so the indicator reports `NOT MONITORED` rather than a health claim it cannot
  substantiate. Use Windows/site monitoring for lighting.
- **Disk health is measured but does not gate production.** The health bar and
  the Diagnostics storage bar report real free space on the station data
  volume, and the indicator faults below 2 GB or 5% free. A low-disk condition
  is reported to the technician only; it does not change station run state,
  which remains a change-controlled contract.
- **Camera Auto can use a labeled demo fallback.** Production stations should
  select Basler required after commissioning if absence of the camera must keep
  the HMI faulted.
- **Visible finish is appearance-based.** It needs representative silver/brass,
  oxidation, supplier, exposure, and lighting qualification and cannot prove
  metallurgy.
- **Model performance is site-specific.** The application supplies the
  workflow and gates, not a universal qualified model.
- **No model weights are in the installer.** Every station must receive or train
  the approved production package and receive the separate base checkpoint if
  offline training is required.
- **No persistent PASS traceability.** This is intentional; add external
  traceability only through a separately approved requirement.
- **No built-in Windows service or automatic startup policy.** Configure any
  kiosk/autostart/recovery behavior under the site's Windows management process.
- **No safety rating.** Bypass, heartbeat, and inspection results are operational
  quality signals only.
- **Generated manuals are not tracked by default.** `output/` is Git-ignored.
  Include a revision-matched approved PDF deliberately in the handoff package;
  do not assume an arbitrary local generated file matches v0.25.0.
- **Unsigned installers can trigger SmartScreen.** Production distribution
  should use organizational code signing.

## Project layout

```text
Pole-Position/
|-- README.md                         current project/handoff authority
|-- BUILD_NOTES.md                    current build and schema identity
|-- BUILD_WINDOWS_INSTALLER.cmd       simple CPU installer wrapper
|-- BUILD_WINDOWS_APP.cmd             local fully bundled application build
|-- CONTRIBUTING.md                   development setup and change expectations
|-- LICENSE                           proprietary terms
|-- SECURITY.md                       private vulnerability reporting
|-- SHA256SUMS.txt                    tracked source integrity manifest
|-- config.example.json               station configuration example
|-- pyproject.toml                    package metadata and dev tooling
|-- requirements*.txt                 demo/runtime/training dependency groups
|-- run.py                            source launcher
|-- battery_inspector/
|   |-- main.py                       Qt startup and frozen install check
|   |-- controller.py                 orchestration and state ownership
|   |-- config.py                     persistent station configuration
|   |-- models.py                     recipes/results/domain contracts
|   |-- paths.py                      resource/data roots and disk health
|   |-- baseline.py                   legacy clean-baseline migration
|   |-- evidence.py                   evidence, retention, references
|   |-- ml_training.py                sample store, split, train/export
|   |-- station_transfer.py           backup, staged restore, rollback
|   |-- dataset.py                    grouped, leakage-safe dataset preparation
|   |-- recipe_draft.py               wizard-side recipe drafting
|   |-- roi_geometry.py               taught-circle crop contract geometry
|   |-- activity.py                   busy/activity tracking
|   |-- build_info.py                 software build identity
|   |-- ui_state.py                   derived run-state for the HMI
|   |-- table_utils.py                table formatting helpers
|   |-- data/repository.py            SQLite persistence/migrations
|   |-- services/
|   |   |-- camera.py                 Basler and mock acquisition
|   |   |-- plc.py                    pycomm3 and mock PLC adapters
|   |   |-- ml.py                     ONNX package/runtime contract
|   |   |-- markings.py               legacy marking helpers
|   |   |-- vision.py                 locator and inspection pipeline
|   |   `-- workers.py                Qt thread-pool task wrapper
|   `-- ui/
|       |-- main_window.py             fixed HMI shell/navigation
|       |-- pages/                     Overview, Inspection, Recipes, ML, etc.
|       |-- widgets.py                 shared ISA-101 widgets
|       |-- image_widgets.py           ROI drawing and image review
|       |-- palette.py                 role and alarm colors
|       |-- theme.qss                  application stylesheet
|       `-- wizard/recipe_wizard.py    seven-step recipe workflow
|-- packaging/windows/
|   |-- build-installer.ps1           controlled Windows release build
|   |-- build-local.ps1               local build, no pylon runtime or Inno
|   |-- PolePosition.spec             PyInstaller specification
|   |-- PolePosition.iss              Inno Setup installer
|   `-- installer-assets/             non-model installer data
|-- scripts/                           probes, smoke tests, ML utilities
|-- tests/                             automated regressions and fixtures
|-- docs/                              subsystem guides and release notes
|-- models/README.md                   separate model-package instructions
`-- .github/
    |-- workflows/ci.yml               CI definition
    |-- dependabot.yml                 dependency update schedule
    |-- CODEOWNERS                     review ownership
    `-- pull_request_template.md       change checklist
```

## Documentation index

Current subsystem guides:

- [Architecture](docs/ARCHITECTURE.md)
- [Windows installer](docs/WINDOWS_INSTALLER.md)
- [Camera configuration](docs/CAMERA_CONFIGURATION.md)
- [PLC interface](docs/PLC_INTERFACE.md)
- [PLC simulation](docs/PLC_SIMULATION.md)
- [Recipe editing](docs/RECIPE_EDITING.md)
- [ML classification](docs/ML_CLASSIFICATION.md)
- [ML Training wizard](docs/ML_TRAINING_WIZARD.md)
- [Storage policy](docs/STORAGE_POLICY.md)
- [Backup and restore](docs/BACKUP_RESTORE.md)
- [HMI philosophy](docs/HMI_PHILOSOPHY.md)
- [HMI style guide](docs/HMI_STYLE_GUIDE.md)
- [UI contract](docs/UI_CONTRACT.md)

Project governance:

- [Contributing](CONTRIBUTING.md) — development setup, the checks CI runs, and
  what a behavior change must update
- [Security policy](SECURITY.md) — private vulnerability reporting and the
  scope notes specific to this application
- [License](LICENSE) — proprietary; no rights are granted by receiving a copy

Release notes under `docs/RELEASE_NOTES_v*.md` are historical records. Older
commissioning documents and `IMPLEMENTATION_ROADMAP.md` are useful design
history but are not the current as-built authority where they conflict with
this README, current source, or current subsystem documents.

## Release history

| Release | Main change |
| --- | --- |
| v0.23.4 | Absolute-path Inno Setup compilation; successful final installer path |
| v0.24.0 | Recipe-editor gate preservation; rejecting terminal marked red; page layout no longer compresses on scaled displays; CUDA PyTorch retained through the build |
| v0.25.0 | Optional PLC result-acknowledge handshake, off by default |
| v0.26.0 | Live camera preview; white balance, black level and gamma exposed |
| v0.27.0 | Validation counts a different confirmed part or a moved one; validation sample count is a station setting; ML Training and Settings behind a passcode; wheel cannot change values |
| v0.28.0 | PLC selector decides the recipe every trigger; unresolvable selection refused and drops Ready; optional PLC station-readiness tag; Logout separate from Exit |
| v0.29.0 | Recipe source is a station setting and a blank selector is refused, not defaulted; triggered-snapshot acquisition; one selector value names exactly one recipe |
| v0.30.0 | ML model package and full recipe package transfers between stations |
| v0.31.0 | Failure Review: triage, keep-from-retention, technician-labelled crops to ML training, export, clear |
| v0.23.3 | Excluded ONNX Runtime example model files |
| v0.23.2 | Excluded ONNX backend test model corpus and improved Inno discovery |
| v0.23.1 | Fixed Windows PowerShell Python probe and defaulted to `python` |
| v0.23.0 | Offline Windows x64 installer with full training stack and no weights |
| v0.22.0 | Verified workstation ZIP backup, staged restore, and rollback |
| v0.21.1 | Pole Position branding and application icon |
| v0.21.0 | Recipe-controlled SILVER/BRASS terminal-finish gate |
| v0.20.1 | Confirmed Overview session-counter reset |
| v0.20.0 | Four-class model with fail-closed `INVALID_MARKING` |
| v0.19.0 | Authoritative PLC mode, PLC-tag trigger, numbered recipes, name/integer selector |
| v0.18.0 | Memory-first PASS, retained failures, binary PLC Pass/Fail |
| v0.17.1 | Prevented PLC-only Save & Apply from validating stale ML fields |
| v0.17.0 | Clean commissioning baseline and taught-circle three-class workflow |
| v0.16.0 | PLC heartbeat and bypass |
| v0.15.0 | Physical terminal-face validity gate |
| v0.14.0 | Taught-circle masked-square ML input contract |
| v0.13.0 | Candidate installation separated from recipe qualification |
| v0.12.x | Multi-ROI capture, review, and leakage-safe grouped dataset preparation |
| v0.11.0 | Guided in-HMI ML training |
| v0.10.0 | ONNX marking classification and exact model binding |
| v0.9.x | Light ISA-101-aligned HMI and compatibility fix |
| v0.8.x | Rotation-invariant terminal-head logic and real-cycle regressions |
| v0.7.0 | Reference registration, real grading, and configuration-bound validation |
| v0.6.0 | Fresh-frame acquisition and explicit inspection state machine |

## Handoff checklist

Do not consider the handoff complete until the receiving owner has all of the
following or an explicit note that the item is not applicable.

### Software and source

- [ ] Current source archive at a recorded Git commit
- [ ] Git bundle or remote repository containing full history and tags
- [ ] `SHA256SUMS.txt` verified
- [ ] v0.31.0 installer, `.sha256`, and requirements lock
- [ ] Exact official Basler pylon redistributable used to build the installer,
      with version and SHA-256
- [ ] Code-signing status/certificate owner recorded
- [ ] Current approved user manual PDF, revision-matched to the application
- [ ] Current release/build/acceptance records

### Station state and controlled assets

- [ ] Fresh Pole Position workstation backup ZIP and its SHA-256
- [ ] Qualified production ONNX and matching JSON manifest
- [ ] Training base checkpoint if each station must train offline
- [ ] Dataset/model evaluation and challenge-set report
- [ ] Active recipe number, name, revision, reference hash, and validation status
- [ ] Camera profile and physical camera/lens/lighting/mount information
- [ ] PLC route, tag map/types, selector mode, poll/heartbeat timing, and program
      backup/export
- [ ] Known-good and controlled reject parts or image/evidence set
- [ ] Retention limits and backup location

### Ownership and operations

- [ ] Production owner
- [ ] Quality/vision owner
- [ ] Controls owner
- [ ] Maintenance owner
- [ ] Software/release owner
- [ ] Windows/IT owner
- [ ] Approved bypass procedure
- [ ] Recovery, escalation, backup, restore, and decommissioning procedures
- [ ] FAT/SAT results and unresolved deviations

Suggested handoff record:

```text
Station / asset ID:
Physical location:
Application version:
Git commit:
Installer SHA-256:
Dependency lock:
Pylon version / SHA-256:
Camera model / diagnostic serial:
Lens / working distance / lighting:
PLC model / program revision / route:
PLC recipe selector: name | number
Active recipe number / name / revision:
Recipe configuration hash:
Model ID / version / ONNX SHA-256:
Training checkpoint / dataset revision:
Failure retention: days / GB
Latest station backup / SHA-256 / date:
Last FAT/SAT date and report:
Known deviations:
Production owner:
Quality owner:
Controls owner:
Maintenance owner:
Software owner:
```

The safest workstation transfer is: install the controlled release, restore a
verified Pole Position workstation ZIP, recommission hardware/PLC, and rerun
known-good/known-reject acceptance. Copying a source directory or frozen EXE
folder alone is not a workstation migration.
