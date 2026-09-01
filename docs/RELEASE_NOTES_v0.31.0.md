# Pole Position v0.31.0

v0.31.0 adds a Failure Review screen: browse retained rejects, hold them back
from retention, add their crops to ML training, export them, and clear them.
Inspection logic, the PLC contract, storage policy, and the recipe and model
formats are unchanged.

## Failure Review

Every non-PASS product cycle already wrote a database row carrying the complete
result and an evidence folder on disk. None of it was reachable from the HMI:
the detail screen only ever showed the *last* result the controller pushed to
it, and the repository had no way to list anything. So in practice the rejects
that would have told you a recipe or the lighting had drifted aged out of the
retention window unread.

**Failures** is a work queue. Retained rejects newest first, with filters for
triage state, age, and reason text. Selecting a row and pressing `OPEN` renders
that part in the same detail view the operator saw live -- the same terminal
cards, crops, class scores and reason -- and `BACK` returns to the queue rather
than to Overview.

Each record carries a triage state, so nobody re-reads the same twenty every
shift:

| State | Meaning |
| --- | --- |
| NEW | Nobody has looked at it |
| REVIEWED | Someone looked. Who and when are recorded |
| SENT TO TRAINING | Its crops were added to the ML training set |

### The four actions

**KEEP / RELEASE** holds a record back from retention. The failure worth
investigating is usually the one somebody is still working on, and it was also
the one most likely to age out of the window before they got to it. A held
record survives both the age and capacity passes until released. KEEP never
applies to PASS evidence: production PASS is memory-only and is removed
unconditionally.

**ADD TO ML TRAINING** takes the part's terminal crops into the training set.
The crop comes from the stored full-resolution frame using the terminal outline
recorded for that cycle, re-cropped through the same `ml_input_crop` contract a
live capture uses, so a sample added here is indistinguishable from one
captured on the ML Training page.

> **The technician labels it, never the model.** The dialog preselects nothing
> and shows the station's reading only as context. A rejected part is exactly
> the case where the classifier may have been wrong -- often the reason it is
> worth training on at all -- so defaulting to the detected class would train
> the model on its own mistakes and entrench them. An operator clicking
> straight through adds no samples rather than adding mislabelled ones.

**EXPORT SELECTED** writes the records and their evidence as one checksummed
ZIP with a summary index, using the same manifest format as the v0.30.0
packages. A record whose evidence retention has already removed is listed in
the index as MISSING rather than dropped silently.

**CLEAR SELECTED** deletes the evidence and the rows. It confirms first, and
the confirmation names how many of the selection are held from retention and
how many have never been exported. Deletion is scoped by the same rule
retention uses -- only a two-level cycle directory beneath
`runtime/inspections/` carrying a readable manifest -- so a recipe reference,
validation evidence, an ML sample, or an installed model cannot be removed
through it whatever is passed. An empty selection is a no-op: clearing never
acts on the whole list implicitly.

Every one of these is written to the audit log with counts.

## Supporting changes

- `InspectionResult.from_dict` and a matching terminal rebuild, so a stored
  record can be rendered by the live detail widgets. Derived keys in the stored
  payload (`passed`, `marking_pass`, and the rest) are deliberately ignored on
  read: they are properties computed from the fields, so trusting them would
  let a hand-edited record claim an outcome its own data does not support. A
  record with no stored geometry falls back to the whole frame rather than
  refusing to open -- it is still evidence that a part rejected.
- The review queue refreshes when a reject is recorded and when the screen is
  opened. Building the handbook figure caught this: the station had just
  rejected a part and the queue still read zero, because it only reloaded on a
  triage action or a manual REFRESH.
- `scripts/capture_manual_screenshots.py` unlocks the maintenance screens
  before navigating. It has been hanging on the passcode prompt since the gate
  landed in v0.27.0, which nothing noticed because the manual build reuses the
  figures already on disk.
- The nav sidebar carries ten entries now, so `NavButton` is 62px rather than
  66px. At 1280x760 the column no longer fitted; `test_layout_fits` measures it.

## Upgrade notes

- The `inspections` table gains triage columns in place. An existing station
  upgrades on first launch with every retained failure starting as NEW.
- No recipe, model, configuration, or evidence migration. Nothing on disk moves.
- PASS remains memory-only, so Failure Review can only ever show non-PASS
  cycles. A false *pass* still leaves no record to review; chasing one means the
  known-good/known-bad part run, not this screen.
- Handbook is Rev E and has a new operator section; the ICD is unchanged.
