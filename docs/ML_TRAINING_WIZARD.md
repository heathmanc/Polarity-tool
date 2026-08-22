# Guided ML Training Wizard

## Purpose

The ML Training page is the controlled technician/engineering workflow for building the global terminal-top classification dataset and creating a candidate polarity model without browsing inspection evidence folders or running dataset scripts manually.

The deployed classifier receives only the isolated metal terminal top and reports:

- `PLUS`
- `MINUS`
- `BLANK`
- `INVALID MARKING`

Physical positive/negative terminal identity and red-ring detection remain independent recipe checks.

The dataset is **global across recipes**. Capture representative terminal tops from every battery family, supplier, terminal construction, finish, stamp depth, and condition that the station may encounter. When new terminal types are added later, retain the existing images and retrain using the accumulated dataset rather than training only on the newest family.

## HMI workflow

The fixed, scrollbar-free wizard has five steps.

### 1. Capture multiple terminal tops from one frame

1. Place a representative battery under the physical camera.
2. Select **CAPTURE FRESH FRAME**.
3. Two dashed circles are pre-created for the common PLUS/MINUS two-terminal battery.
4. Move or **REDRAW ACTIVE CIRCLE** around the first flat metal terminal face.
5. Assign its actual class: PLUS, MINUS, BLANK, or INVALID MARKING.
6. Select the second circle, place/redraw it around the second terminal face, and assign its actual class.
7. Use **+ ADD CIRCLE** when more terminal tops are visible in the same frame. A fixed maximum keeps the HMI scrollbar-free.
8. Select any circle row or click a circle on the image to make it active. The full camera image remains the single capture view.
9. Optionally enter a **Battery / terminal family** tag such as `Group31 / supplier A`. This is metadata only; it does not change the selected ML class.
10. Select **SAVE ALL CIRCLES**. All circles are validated before any new sample is committed.
11. Move or rotate the battery and capture another fresh frame for additional variation.

All circle-derived crops from one frame share one capture-group ID and are forced into the same train/validation/test split.

### ML input rule

Each circle must surround the flat metal terminal face and stamped marking only. Keep the following outside the circle:

- red polarity ring;
- molded or printed battery-case `+/-` symbol;
- washer;
- outer hex hardware;
- unrelated case geometry.

There is intentionally **no red-ring verification checkbox** in ML Training. Red-ring inspection remains a separate production measurement and must not become an ML shortcut.

Use **INVALID MARKING** only when the physical terminal face is present but the
visible pattern is not an acceptable PLUS, MINUS, or BLANK. Suitable examples
include damaged, partial, malformed, doubled, or unrelated stamp patterns. Do
not use it for glare, blur, bad exposure, or low-confidence images; those remain
NO DECISION. Do not use it for an absent terminal; the physical-input gate owns
that failure.

The circle is converted to a square image for YOLO/ONNX. Pixels outside the circle are neutralized using the median color measured inside the circle. Training and production both use this same `taught_circle_masked_square_v1` contract.

The capture page deliberately does **not** show a separate crop preview. When several terminal circles are present, the full camera frame with all labeled dashed circle overlays is the authoritative visual. This keeps the fixed HMI pane uncluttered and makes it easier to confirm all circles together.

### 2. Review / correct dataset

The page is a persistent, paginated image browser. It shows six stored terminal-top crops per page with no scroll bars. You can filter by class or Battery / terminal family, correct a mislabeled sample, or remove a bad crop before preparing/retraining. Dataset edits are written back to the persistent sample manifest immediately.

The header also shows total sample count, independent camera-frame groups, and represented family tags.

Current collection guidance:

| Class | Advisory collection target |
| --- | ---: |
| PLUS | 100+ independent captures |
| MINUS | 100+ independent captures |
| BLANK | 100+ independent captures |
| INVALID MARKING | 100+ independent captures |

These are **targets, not hard stops**. The wizard never blocks Review or Prepare
because counts are below target. Four-class training still requires at least one
labeled training example of each class and a leakage-safe validation group.
Production qualification should continue accumulating multiple physical
batteries, suppliers/lots, stamp angles, terminal finishes, positions,
illumination conditions, scratches, oxidation, and diverse invalid patterns.

### 3. Prepare

**PREPARE DATASET** creates leakage-safe `train`, `val`, and `test` class folders beneath the station runtime data directory. The technician does not need to locate or edit those folders.

The current workflow uses the taught-circle
`taught_circle_masked_square_v1` contract only. PREPARE DATASET includes every
stored PLUS/MINUS/BLANK/INVALID_MARKING circle sample. Existing valid
PLUS/MINUS/BLANK samples remain usable after upgrading. If an incompatible
pre-v0.17 record appears, preparation stops explicitly instead of silently
training on a small subset.

**CHECK TRAINING RUNTIME** verifies Ultralytics, PyTorch, ONNX export support, ONNX Runtime verification support, and CUDA availability. NVIDIA hardware is reported separately from PyTorch CUDA capability so a CPU-only PyTorch wheel on a GPU workstation is obvious.

### 4. Train

The technician selects:

- Ultralytics classification base model or local `.pt` file;
- epochs;
- input image size;
- batch size;
- CPU or detected CUDA device;
- model ID and version.

**START MODEL TRAINING** runs in a background worker. Normal inspection cycles are held while training is active. The worker trains with full rotation augmentation, exports the best model to ONNX, and evaluates it automatically on the held-out test split using the production confidence/margin logic.

### 5. Deploy

The HMI shows held-out:

- image count;
- acceptance rate;
- accuracy including low-confidence abstentions;
- accepted-result accuracy;
- per-class recall.

Candidate installation and production qualification are deliberately separate. **INSTALL CANDIDATE FOR RECIPE VALIDATION** is enabled when the exported ONNX package passes the normal production loader/runtime self-test. Held-out class coverage and performance targets remain visible commissioning warnings; if they are incomplete or below target, the HMI requires an explicit confirmation before installing the engineering candidate.

Installing a model does **not** make an existing recipe valid and does not modify active recipe revisions. Open **RECIPES**, select the battery, and choose **CREATE ML REVISION** / **EDIT / NEW REVISION**. The new revision binds to the exact model ID/version/SHA-256 and must complete guided physical validation before production activation.

If training exported ONNX successfully but failed later because ONNX Runtime was missing, the exported candidate is retained. After repairing the runtime, select **LATEST CANDIDATE** instead of retraining.

## Data safety rules

- ML capture requires a physical camera; demo images and automatic camera fallback cannot create training samples.
- Multiple circles from one frame are saved as a single logical capture batch.
- All circles are validated before the batch is appended to the dataset manifest.
- Duplicate image bytes in the same class are not counted twice.
- Stored samples can be reviewed, relabeled, or removed from the HMI before the next dataset preparation/training run.
- Multiple samples from one fresh full frame remain in the same dataset split.
- The optional battery/terminal-family tag is metadata, not a model class.
- Low-confidence or poor-quality production inference remains fail-closed as **NO DECISION**. This is an inspection state, not a trained ML class.

## Training runtime on Windows

From the project folder in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-training.txt
```

For Command Prompt:

```bat
.venv\Scripts\activate.bat
python -m pip install -r requirements-training.txt
```

GPU-specific PyTorch/CUDA installation should follow the engineering workstation standard; the HMI intentionally does not install or replace GPU drivers/packages automatically.
