# Pole Position v0.31.1

Fixes OPEN on the Failure Review screen, which did nothing.

## OPEN did nothing

Selecting a retained failure and pressing `OPEN` left the screen on the queue
with no message and no error.

The main window handed the detail card the record's `TerminalInspection`
objects where it expected the recipe's `TerminalRecipe` objects. The card
guarded that field against `None` but not against the wrong type, so rendering
raised `AttributeError` reading recipe-only geometry (`marking_roi_shape`). The
exception came out of a Qt slot part-way through drawing: navigation never
happened, and nothing was reported.

Two changes:

- The detail card now stores anything that is not a `TerminalRecipe` as `None`.
  The overlay is decoration; the expected-versus-detected text beside it is the
  result. A caller passing the wrong object loses a rectangle, it does not take
  down the screen that explains why a part rejected.
- A retained failure is opened with **no** recipe geometry, deliberately. The
  stored record names the recipe but not the revision that graded the part, so
  drawing a marking ROI from whatever revision exists now could put a rectangle
  from one revision over a crop taken under another. The stored crops already
  show what was analyzed. Returning to the live view restores its geometry, so
  the next live result is drawn with overlays as before.

**Why the tests missed it.** The v0.31.0 test asserted only that the page
emitted the record; it never drove the render. A hand-built payload would not
have caught it either — the crash is inside the branch that only runs when the
stored crop files exist on disk, which they do only after a real cycle. The
test added here runs a real inspection through the pipeline, opens the record
from the queue, and asserts the detail view shows that record's disposition and
reason.

## Upgrade notes

No data, configuration, or contract change.
