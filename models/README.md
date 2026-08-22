# Polarity ML model package

The production station looks here by default for:

```text
models/polarity_classifier.onnx
models/polarity_classifier.json
```

Do not commit a production model until it has been reviewed and versioned for the
site. `scripts/train_marking_classifier.py` creates both files. The JSON manifest
contains the model ID/version, exact ONNX SHA-256, class order, input size, and
preprocessing contract. Recipes using ML snapshot the model ID/version/hash when
the recipe revision is validated; replacing the model therefore requires a new
recipe revision and guided validation.

Current models have exactly four physical classes:

```text
plus
minus
blank
invalid_marking
```

`INVALID_MARKING` means the terminal face is physically present but its visible
pattern is not an acceptable PLUS, MINUS, or BLANK. It always rejects. A
missing/invalid terminal face is rejected by the physical-input gate before ML.

`NO DECISION` is a runtime fail-closed outcome produced by low confidence,
insufficient class margin, poor image quality, or an invalid crop. It is not a
training class. Supported legacy three-class and `unreadable` packages remain
loadable only for recipe revisions already qualified with those contracts. A
new or edited recipe revision can bind only to the current four-class package.

New models use the `taught_circle_masked_square_v1` input contract. The
technician draws a circle around the flat metal terminal face; the application
converts it to a square and neutralizes everything outside the circle before the
sample is stored. The exact same crop contract is used by model probing, recipe
validation, and production inspection.

The classifier must never be trained on a crop that exposes the red polarity
ring or molded case `+` / `-` symbols, because those would be unsafe shortcuts.
