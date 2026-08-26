# Pole Position v0.27.0

v0.27.0 changes what makes a recipe validation sample count, makes the number
of samples a station setting, puts ML Training and Settings behind a passcode,
and stops the mouse wheel changing values. Inspection logic, the PLC contract,
storage policy, and the recipe and model formats are unchanged.

## Recipe validation on a fixed-stop fixture

**Action required if you validate recipes at a hard stop.** The rule for what
counts has changed, and the change is what makes those stations able to
validate at all.

A sample used to count only if the battery's **pose** differed from every
sample already counted -- position, rotation, or scale. On a fixed-stop
fixture that is unachievable, and for the reason the fixture exists: the stop
is there to make the pose repeatable, so requiring a different pose asked the
technician to defeat it.

A sample now counts when it is **independent** of the samples already counted,
by either of two routes:

- **A different physical battery**, confirmed by the technician in the wizard.
  The confirmation is recorded against the sample, so the evidence states the
  basis on which it counted.
- **A different pose**, exactly as before, needing no confirmation.

What is still refused is the same part in the same place: that is one piece of
evidence counted twice, and it would let a recipe qualify on a single frame
repeated.

A different battery is also the better evidence. Part-to-part variation in
stamp depth, finish, and ring is what varies in production; pose variation at a
hard stop is what the fixture removes.

### The sample count is now a station setting

**Settings → GENERAL → Recipe validation samples**, 1 to 50, default 5. It
describes how thoroughly this site qualifies a recipe, so it is station-wide
rather than per battery. An existing recipe keeps the count it was validated
against until it is revalidated, so reopening a recipe never quietly restates
how thoroughly it was qualified.

## ML Training and Settings are behind a passcode

Both screens ask for a maintenance passcode. The shipped default is `PP26`,
and it can be changed per station.

**This is a speed bump, not a security control, and it should not be described
as one to an auditor.** Anyone with the workstation's file system, the
installer, or the source can bypass it, and a four-character passcode is short
enough to guess. The Windows account, physical access to the station, and the
audit log remain the real controls.

What it does buy:

- opening a maintenance screen becomes a deliberate act rather than a mis-tap
  on a touchscreen;
- **every unlock and every refusal is written to the audit log**, so "who
  opened Settings before that recipe changed" has an answer, and so does "who
  was trying to".

Details:

- One unlock covers both screens for the session. LOGOUT locks them again.
- Production screens -- Overview, Inspection, Recipes, Diagnostics, Events --
  are never gated. An operator is never asked for a passcode to do their job.
- The passcode is stored salted and hashed, with a salt generated per station.
  Not because the hash resists attack, but so a config file read over a
  shoulder, pasted into a ticket, or carried in a backup does not display it.
- A configuration with no passcode fails closed: the screens stay locked
  rather than opening.

## The mouse wheel no longer changes values

Scrolling over a spin box or a combo box changed it. On a station that
silently rewrote an exposure, a retention limit, a recipe number, or an
expected terminal marking, and the change was indistinguishable from a
deliberate one. Wheel events are now refused over any control that holds a
value, in every window and dialog. Typing, the arrows, and scrolling elsewhere
are unaffected.

## Verification

414 pytest test functions, expanded by parameterization to 448 collected
cases, and four command-line smoke and installation checks pass.

One regression was caught during this work and is worth recording: seeding the
default passcode initially wrote it back during `AppConfig.load`, which changed
config.json after a backup manifest had been checksummed and aborted a staged
restore -- the same class of failure that stranded a station earlier in this
project. Loading a configuration now never modifies it, and a test pins that.

Test results from the release build environment must still be recorded
separately. Passing tests are not site acceptance.
