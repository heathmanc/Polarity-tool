# Pole Position v0.28.0

v0.28.0 makes the PLC decide the recipe on every trigger, publishes station
readiness to the controller, and separates Logout from Exit. Inspection logic,
storage policy, the result contract, and the recipe and model formats are
unchanged.

## The PLC names the product on every trigger

**Action required for controls engineers. Re-read the ICD before the next
run.** The recipe selector changed from a permissive into a selector.

The station used to hold one recipe, activated by a technician at the HMI. The
selector tag was compared against it, and a trigger naming anything else was
logged and ignored. That made a mixed line impossible without somebody standing
at the HMI to activate the right recipe between products, and it made headless
operation impossible outright.

Now the selector **decides** the recipe. On every trigger the station resolves
the received name or number to the **newest revision of that recipe whose
validation is complete**, and grades the part against that revision. What is
resolved is what grades the part; nothing else does.

- There is no activation step. Saving a validated revision puts it into
  production on the next trigger that names its recipe.
- There is no station-side recipe selection in the PLC path, so a mixed line
  needs no operator intervention and the station can run headless.
- Recipe numbers remain stable across revisions, so the controller keeps naming
  the same number as recipes are revised.
- Draft and retired revisions are never resolved. A newer draft does not
  displace the validated revision that is running.

### An unrunnable product is refused, never substituted

If the received name or number is unknown, or every revision of it is a draft
or retired, the station logs the refusal and ignores that trigger edge. It does
not grade the part against another recipe, and it does not publish a synthetic
Fail. `Busy` and `Complete` stay where they were, and the controller must keep
the product inhibited and apply its own timeout.

`BatteryVision.Ready` goes false for as long as the selector names an
unrunnable product, so a misconfigured line is visible as a state before a
trigger is ever sent, rather than only as a timeout.

### The bench and simulation path is unchanged

A manual inspection started at the HMI, and any station with no selector tag
configured, still grades against the recipe selected on the Recipes page. That
selection is what the ACTIVATE control now does, and it is labeled **USE FOR
MANUAL TRIGGERS** to say so.

### What changed on screen

- The Overview header metric is now **Recipe**, and it shows the recipe that
  graded the last part rather than a station selection that may have had
  nothing to do with it.
- The recipe wizard's final action reads **SAVE FOR PRODUCTION**, and its
  warning states plainly that a validated revision is used as soon as it is
  saved.

## Station readiness on the wire

`BatteryVision.Ready` (optional, configured under Settings -> PLC TAGS) reports
whether the station could grade a part if it were triggered right now. It is
false when the camera is absent or faulted, when the recipe the selector names
cannot be resolved or has a blocking readiness issue, and while the camera is
held by something that is not an inspection: a live preview, a reference
capture, a validation run, an ML training capture, or a settings apply.

Those last states are exactly the ones where a trigger was previously dropped
in silence. The controller's permissive is:

```
Safe_To_Trigger := BatteryVision.Ready AND NOT BatteryVision.Busy;
```

Ready deliberately stays true during a cycle -- `Busy` already reports that, and
a readiness bit that dropped every cycle would flap at cycle rate. The tag is
written only on transitions.

## Logout and Exit are separate

Logout locks ML Training and Settings behind the maintenance passcode and
returns to Overview, leaving the station running. Exit closes the program. They
were previously one control that closed the HMI.

## Upgrade notes

- No recipe, model, configuration, or record migration.
- Recipes validated under earlier releases resolve unchanged; their validation
  records already carry the configuration hash and engine binding that
  resolution requires.
- Confirm the recipe selector tag is configured and correct before the first
  run: with no selector configured the station falls back to the HMI selection,
  which is the bench behavior, not the production behavior.
- Configure `BatteryVision.Ready` and wire it into the trigger permissive.
- Regenerate the controls handout (`scripts/build_plc_icd.py`) and reissue it;
  the recipe-selector and Ready sections both changed.
