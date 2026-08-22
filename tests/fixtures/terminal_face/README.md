# Terminal-face validity regression fixtures

These images support the v0.15 physical-input gate.

- `valid_negative_current.png` / `valid_negative_reference.png` are a real accepted negative-terminal pair.
- `valid_positive_current.png` / `valid_positive_reference.png` are a real accepted positive-terminal pair.
- `missing_terminal_face.png` is cropped from the operator-supplied field screenshot where an open/missing terminal face incorrectly received a high-confidence MINUS result before the physical-input gate existed.

The regression requirement is that the two valid pairs remain accepted while the missing-face image is rejected before polarity classification.
