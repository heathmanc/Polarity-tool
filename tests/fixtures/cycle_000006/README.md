# Cycle 000006 regression fixture

These crops came from the user-supplied production evidence package
`CYCLE-20260819-140953-386559-000006.zip`.

The battery is physically correct:

- negative terminal: MINUS, no red ring;
- positive terminal: PLUS, red ring present.

The negative terminal head rotated independently of the battery between the
reference capture and the inspected part. The v0.7 template-only classifier
false-rejected it because the stamp angles differed by roughly 58 degrees.
This fixture must remain a rotation-invariance regression test.
