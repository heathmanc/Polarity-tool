## What changed

<!-- What this does and why. Link the requirement or issue if there is one. -->

## Verification

<!-- Delete rows that genuinely do not apply, and say why in "What changed". -->

- [ ] `python -m pytest`
- [ ] `python scripts/vision_smoke_test.py`
- [ ] `python scripts/stamp_rotation_smoke_test.py`
- [ ] `python scripts/terminal_top_gate_smoke_test.py`
- [ ] `python -m ruff check battery_inspector scripts tests`
- [ ] `python scripts/verify_source_checksums.py --write` run and the updated
      `SHA256SUMS.txt` committed

## Inspection behavior

- [ ] This change cannot alter a PASS/REJECT decision, **or** the graded
      behavior change is intended and covered by a regression that fails
      against the previous code.
- [ ] The README's change-control invariants are preserved, **or** the
      replacing requirement is named below.

<!--
A behavior change also updates BUILD_NOTES.md, the appropriate release note,
the README, and the version declarations. See CONTRIBUTING.md.
-->

## Station impact

<!-- Anything a commissioned station needs on upgrade: recipe revalidation, a
     new model package, configuration migration, or a re-run of the acceptance
     scope. Write "none" if there is none. -->
