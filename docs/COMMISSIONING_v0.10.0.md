# v0.10.0 ML commissioning checklist

This checklist assumes the camera, PLC simulation/pycomm3, reference
registration, recipe wizard, and light HMI have already been commissioned.

## 1. Update the runtime

```powershell
python -m pip install -r requirements.txt
python -c "import onnxruntime; print(onnxruntime.__version__)"
python run.py
```

The HMI must still start with no model files present. Existing legacy recipes
must remain usable.

## 2. Collect safe terminal-top data

- Use known-good recipe validation captures and production PASS evidence.
- Verify automatic export uses `terminal_top.png` for recent evidence.
- Add manually reviewed UNREADABLE samples.
- Do not train on full positive-terminal crops that show the red ring or molded
  case `+`.
- Include terminal-head rotations through the physically possible range.

## 3. Prepare and review the dataset

```powershell
python scripts\export_marking_dataset.py --data-dir runtime --output dataset\markings --clean
python scripts\prepare_ml_dataset.py --clean
```

Review `dataset\polarity_cls\summary.json`. Do not train until every class is
represented in both train and validation. Keep an additional site challenge set
outside these folders for final acceptance.

## 4. Train/export on an engineering workstation

```powershell
python -m pip install -r requirements-training.txt
python scripts\train_marking_classifier.py --device 0
```

If the network blocks automatic base-weight downloads, provide a local weights
file with `--base-model`.

## 5. Probe the package

```powershell
python scripts\ml_model_probe.py
```

Confirm:

```text
ready: true
classes: plus, minus, blank, unreadable
model SHA-256 shown
input size shown
```

## 6. Install through the HMI

Open **Settings -> VISION / ML** and use **APPLY & TEST ML MODEL**. The HMI must
reject a model whose JSON hash does not match the ONNX file.

## 7. Create an ML recipe revision

Edit the target recipe. On the Polarity page confirm:

```text
CLASSIFICATION ENGINE: ML / ONNX
<model ID> <model version>
```

Capture/retain the correct reference, confirm battery and terminal ROIs, and run
all guided validation samples. Existing validation from the legacy classifier
must not be counted for the ML-bound revision.

## 8. Challenge tests before activation

At minimum test:

- known-good PLUS/MINUS;
- PLUS/MINUS at varied terminal-head angles;
- expected BLANK;
- reversed polarity stamping;
- red ring on wrong physical terminal;
- scratches resembling one arm of a plus;
- glare and partial saturation;
- oxidation/dirty terminal top;
- intentionally out-of-focus/unreadable image;
- battery translated/rotated within the normal station envelope;
- no battery / partially visible battery.

Uncertain classification must become UNREADABLE/REJECT or NOT READY according
to the surrounding condition; it must never be converted to PASS.

## 9. Archive qualification evidence

Record the approved ONNX SHA-256, manifest, training dataset version, held-out
challenge results, recipe revision, camera/lighting configuration, and date.
Never overwrite an approved ONNX file in place; create a new model version and
revalidate recipe revisions against its new hash.
