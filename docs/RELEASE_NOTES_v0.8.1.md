# Polarity Tool v0.8.1 Release Notes

## Summary

v0.8.1 is a targeted, safety-conscious correction for the false reject captured in `CYCLE-20260819-164558-840160-000011`.

The v0.8.0 engine correctly measured the positive terminal as a strong PLUS:

```text
Geometry class:       PLUS
Geometry confidence:  95.9%
Primary line signal:  0.576
Orthogonal ratio:     0.759
Intersection offset:  4.4%
Red ring:             PRESENT
```

However, the taught marking ROI was slightly off-center. The selected Hough circle received a 73.8% terminal-top confidence against an 80.0% hard gate. v0.8.0 therefore ignored the correct geometry, used the older template fallback, and false-rejected the battery.

## Conditional terminal-top gate

The terminal top can now be used in one of three states:

```text
NOMINAL
CONDITIONAL
REJECTED / FALLBACK
```

A below-nominal top is conditionally accepted only when all of the following are true:

- a real `HOUGH_CIRCLE` was found;
- the top-lock confidence meets the conditional minimum;
- the observed class is PLUS or MINUS;
- independent geometry confidence is strong;
- the correct PLUS/MINUS geometry sanity gate passed;
- the stamp geometry is centered within the selected terminal top;
- the terminal-top circle is fully visible;
- image quality passed;
- later hybrid template-conflict, confirmation, confidence, and margin gates also pass.

Conditional acceptance is deliberately not available for:

- BLANK decisions;
- `CENTER_FALLBACK`;
- ambiguous or weak geometry;
- partially visible terminal tops;
- missing terminal-top images;
- strong template/geometry conflicts.

## Defaults

```text
Nominal terminal-top confidence:       0.80
Conditional top confidence:            0.68
Conditional geometry confidence:       0.90
Conditional stamp center score:        0.55
Conditional circle inside fraction:    0.90
```

These are recipe-versioned classifier settings. They are engineering controls and are not exposed as normal technician inputs.

## HMI and evidence

Inspection Detail now renders terminal-top lock as one of:

```text
91.5%  NOMINAL
73.8%  CONDITIONAL ACCEPT
62.0%  FALLBACK
```

The evidence manifest records:

```text
terminal_top_acceptance
terminal_top_conditionally_accepted
terminal_top_gate_reason
terminal_top_center_offset_fraction
terminal_top_inside_fraction
conditional thresholds used for the decision
```

The accepted-result analysis note also states whether the top was nominal or conditional.

## Real-cycle regression

The exact cycle-000011 current/reference marking crops and terminal crops are stored in:

```text
tests/fixtures/cycle_000011/
```

Expected regression result:

```text
Negative terminal: MINUS, NOMINAL, ring absent, PASS
Positive terminal: PLUS, CONDITIONAL, ring present, PASS
Overall: PASS
```

Run it with:

```powershell
python scripts\terminal_top_gate_smoke_test.py
```

## Build identity in source archives

The repository now includes a `git archive` substitution file. A source release created with `git archive` embeds the release commit, allowing `software_build_info()` and evidence manifests to report a Git revision even when the deployed source tree has no `.git` directory.

Runtime priority remains:

1. `POLARITY_TOOL_GIT_COMMIT` environment override;
2. local Git checkout revision;
3. archived/substituted revision;
4. `unknown` only when none of the above is available.

## Recipe validation

The inspection-engine identifier is now:

```text
reference_registration_rotation_invariant_hybrid_v2_1
```

Because this is a safety-relevant acceptance change, previous validation evidence is not silently reused. Create or edit a recipe revision and complete the guided physical validation before activating it under v0.8.1.
