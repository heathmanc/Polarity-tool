# Cycle 000011 regression fixture

This fixture was extracted from the user-provided production evidence package
`CYCLE-20260819-164558-840160-000011` captured with a Basler acA5472-17uc.

The battery is physically correct:

- negative terminal: MINUS, no red ring;
- positive terminal: PLUS, red ring present.

Polarity Tool v0.8.0 correctly measured strong PLUS geometry on the positive
terminal, but its central terminal-top confidence was 0.7379 versus the hard
0.8000 gate. It discarded the correct geometry, fell back to the old template
path, and false-rejected the battery.

The fixture is retained to verify that a real Hough circle with strong,
centered PLUS/MINUS geometry can receive a conditional terminal-top acceptance
without weakening fail-closed behavior for fallback or ambiguous detections.
