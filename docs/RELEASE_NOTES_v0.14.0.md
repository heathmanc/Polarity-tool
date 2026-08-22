# Polarity Tool v0.14.0

## Taught circular marking regions

The marking/classification region is now taught as a circle around the flat metal terminal face rather than a rectangular ROI. This geometry is used in both the ML Training workflow and new/edited recipe revisions.

- Capture one fresh frame and teach multiple circles at once.
- Label each circle PLUS, MINUS, or BLANK.
- Use **REDRAW ACTIVE CIRCLE**, smaller/larger controls, add/remove, and batch save.
- Red-ring verification remains independent and is not part of ML sample labeling.

## Identical training and production input

Each taught circle is converted to a square crop. Everything outside the circle is replaced with the median color from inside the terminal face. This prevents red rings, molded battery-case polarity symbols, washers, and other surrounding context from becoming ML shortcuts.

The input contract is recorded as:

```text
taught_circle_masked_square_v1
```

Circle-based ONNX recipes use this taught region directly. Automatic Hough-circle terminal-top discovery is bypassed. Legacy rectangle ONNX recipes are also corrected to use their exact taught rectangle directly instead of applying a second Hough/recentering transform. This makes training, model probing, recipe validation, and production use the same image contract.

## Compatibility

Existing recipe payloads without `marking_roi_shape` load as legacy rectangular regions. Editing a legacy recipe preserves that rectangle while a legacy model is bound, so the existing model can be revalidated with the crop convention it was trained on. When the installed model declares `taught_circle_masked_square_v1`, new/edited revisions use circle-based primary marking regions and must complete guided validation before activation.

Existing training samples remain readable and are identified as legacy rectangle samples in the dataset summary. They may be reviewed/removed through the built-in dataset browser before retraining.

## Three physical ML classes

The trained classifier now has exactly three semantic classes: `PLUS`, `MINUS`, and `BLANK`. `UNREADABLE` is no longer a training label. Poor image quality, low confidence, or insufficient class margin produces a fail-closed **NO DECISION** inspection state without teaching the network an artificial fourth physical class. Legacy four-class ONNX packages remain loadable for existing validated revisions, but new guided training produces three-class models.

## Exact-crop regression fix

A v0.13 commissioning probe exposed a real contract mismatch: guided training stored the technician-drawn rectangle, while recipe validation could run a second `TerminalTopNormalizer`/Hough-circle transform before ONNX inference. A model that was correct on its training images could therefore collapse to one class during validation.

v0.14 removes that hidden transform from ONNX classification. The `ml_model_probe.py --image` path also sends the supplied saved crop directly to ONNX and has quadrant TTA **off by default**. Use `--tta` only when intentionally qualifying a model with that inference policy.
