# ML polarity classification

Pole Position can classify the stamp on each physical battery terminal with a
small image-classification network deployed through ONNX Runtime. Training and
production are deliberately separated:

- **Training workstation:** Ultralytics/PyTorch may be used to train and export.
- **Production station:** only ONNX Runtime is required for inference.
- **Recipe:** stores the exact model ID, version, and SHA-256 that were used
  during guided validation.

Changing the station model therefore does not silently change a validated
inspection. A recipe bound to a different model hash becomes NOT READY until a
new revision is validated.

## Safety boundary: what the model is allowed to see

The ML model is not given the complete terminal search crop. Pole Position first
locates the central round metal terminal top and then classifies only that
isolated crop. This is intentional.

A complete positive-terminal image can contain a red ring and a molded `+` on
the battery case. If those pixels are included in the training/inference image,
a network can learn the shortcut "red ring = PLUS" instead of reading the stamp.
That would defeat the inspection if the ring were installed around a terminal
whose top was stamped incorrectly.

The production decision now keeps four independent measurements:

```text
physical terminal identity from recipe geometry
                 +
SILVER / BRASS visible finish versus the recipe reference
                 +
PLUS / MINUS / BLANK / INVALID MARKING from isolated metal top
                 +
red-ring YES / NO from independent OpenCV color inspection
                 |
                 v
           recipe comparison
```

The SILVER/BRASS check is conventional reference-anchored color analysis. It is
not another ML class and therefore does not require recipe-specific polarity
models or finish labels in the marking-training dataset.

## Required classes

Every newly trained model must contain exactly these physical labels:

```text
plus
minus
blank
invalid_marking
```

`INVALID_MARKING` means that a terminal face is present, but its observed
pattern is not an acceptable PLUS, MINUS, or BLANK. It is always a reject and
can never be configured as a recipe's expected marking. It is not the missing
terminal class: the physical-input gate rejects a missing/invalid terminal face
before inference.

`UNREADABLE` is not a trained class. Low image quality or insufficient model
confidence/margin produces a separate fail-closed **NO DECISION** inspection
state. Existing three-class packages remain readable for already-bound recipe
revisions, but only the current four-class package can be bound to a new recipe
revision.

A model package that omits a class, adds an unsupported class, has a model hash
that does not match its manifest, exposes an incompatible tensor shape, or
fails an ONNX self-test is rejected during **Settings -> VISION / ML -> APPLY &
TEST ML MODEL**.

## Guided data collection and training in the HMI

v0.11.0 adds a dedicated **ML TRAINING** page. This is now the preferred site workflow; technicians do not need to browse inspection evidence folders or run dataset scripts manually.

The wizard provides five fixed steps:

1. **Capture** a fresh physical-camera frame, adjust the visible terminal-top ROI, and label each circle PLUS/MINUS/BLANK/INVALID MARKING.
2. **Review** class coverage and latest samples.
3. **Prepare** leakage-safe train/validation/test folders and check the local training runtime/CUDA device.
4. **Train** the Ultralytics classification model in a background worker and export/evaluate ONNX automatically.
5. **Deploy** a candidate only after the built-in held-out engineering gate passes.

Samples are written beneath the station runtime `ml_training` area. A fresh full frame can contribute more than one terminal crop, but every crop from that frame retains the same capture-group ID and is forced into the same dataset split.

The HMI displays advisory collection targets rather than a hard image-count
gate. A useful qualification target is roughly 100+ independent captures per
class with meaningful variation, including representative invalid markings.
The wizard permits earlier candidate training, but every class must be present
in training and measured held-out performance determines whether the candidate
is credible.

Training dependencies remain optional:

```powershell
python -m pip install -r requirements-training.txt
```

The wizard detects PyTorch, Ultralytics, ONNX export support, and CUDA devices. It intentionally does not install or replace GPU/CUDA packages automatically.

See `ML_TRAINING_WIZARD.md` for the controlled workflow.

## CLI engineering utilities

The older dataset/export/training scripts remain available as engineering backup tools and for automated workflows. They are no longer required for normal technician model creation.

```powershell
python scripts\export_marking_dataset.py --data-dir runtime --output dataset\markings --clean
python scripts\prepare_ml_dataset.py --clean
python scripts\train_marking_classifier.py --device cpu
python scripts\evaluate_ml_model.py --data dataset\polarity_cls\test
```

Do not use contextual positive-terminal images containing the red ring or molded case `+` as classifier inputs.

## Commission a trained candidate

When the ML Training wizard installs a candidate, the exact ONNX/manifest package is copied into the station runtime model store and loaded through the same model verification used by **Settings -> VISION / ML**. Installing a candidate does not validate any recipe.

1. Install the candidate from the wizard.
2. Create or edit a recipe revision.
3. Confirm the Polarity step shows the intended ML model identity/SHA-256.
4. Run guided recipe validation on known-good physical batteries at varied positions and terminal-head rotations.
5. Challenge reversed markings, wrong ring, blank terminals, scratches, glare, dirt, and unreadable cases before activation.

Existing legacy recipe revisions continue to use their validated legacy classifier until a new revision is explicitly bound to ML.

## Production inference gates

The ML result is not accepted solely because one class is largest. The recipe
contains fail-closed thresholds for:

- minimum top-class probability;
- minimum margin over the second-best class;
- stricter confidence/margin when the terminal-top detector had to use a center
  fallback;
- image contrast, sharpness, and clipping.

A low-confidence or low-margin observation becomes **NO DECISION** (internally represented as the fail-closed unreadable state). It cannot be converted to `BLANK` or PASS.

## Model lifecycle

A model is a controlled inspection asset. Keep the ONNX and manifest together,
record the training dataset/challenge-set results, and do not overwrite an
approved model in place. Use a new model version and new file package. Recipe
validation binds to the SHA-256, so the application detects a file replacement
even if the filename stays the same.

Completing a training run does not change the station model, so the active
recipe continues using its currently installed and bound model. Selecting
**INSTALL CANDIDATE FOR RECIPE VALIDATION** changes the station model. Existing
recipes remain bound to their old model SHA-256 and become NOT READY against the
new station model. Create or edit a recipe revision, bind it to the installed
candidate, and complete guided validation before production resumes. The HMI
never silently carries prior validation onto a different model.

## v0.12.0 multi-ROI capture

The guided HMI can collect several terminal-top crops from one fresh camera
frame. Each circle is independently labeled PLUS/MINUS/BLANK/INVALID MARKING,
then all are saved under one capture-group ID. The splitter keeps same-frame
samples together.

The training dataset remains global across recipes. An optional battery/terminal-family tag is stored for coverage analysis only. Red-ring inspection remains separate from ML classification and there is no red-ring confirmation checkbox in the ML capture workflow.
