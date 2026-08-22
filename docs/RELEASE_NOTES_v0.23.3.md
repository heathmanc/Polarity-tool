# Pole Position v0.23.3

v0.23.3 is a focused Windows packaging correction based on v0.23.2.
Inspection, PLC, recipe, model, training, backup, and production-storage
behavior are unchanged.

## Corrected

- The PyInstaller specification excludes `onnxruntime.datasets`, which contains
  three example `.onnx` files (`logreg_iris`, `mul_1`, and `sigmoid`) supplied
  with ONNX Runtime for examples and tests.
- The controlled frozen-output cleanup removes that exact dependency dataset
  directory if a third-party hook collects it again.
- The final recursive release gate still rejects every remaining `.onnx`,
  `.pt`, or `.pth` file. Production models and base training checkpoints remain
  separate from the installer.
