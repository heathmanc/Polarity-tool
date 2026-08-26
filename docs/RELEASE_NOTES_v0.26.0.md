# Pole Position v0.26.0

v0.26.0 adds a live camera preview and exposes the colour and tone controls the
inspection actually depends on. Inspection logic, the PLC contract, storage
policy, and the recipe and model formats are unchanged, and a station upgrading
from v0.25.0 keeps its data and its existing camera behaviour.

## Live camera preview

The CAMERA IMAGE tab, where exposure lives, now streams frames while a control
is moved. The effect of an exposure, gain, white balance, black level, or gamma
change is visible as it is made, instead of after an apply-and-test cycle.

Driving a production camera this way is only acceptable with guarantees, and
these are tested directly:

- **Nothing can be graded while the preview runs.** The station counts as
  camera-occupied, so a manual trigger is refused and a PLC trigger is dropped
  for the controller's timeout to catch. A part must never be graded on an
  exposure a technician is still dragging.
- **Preview settings are never persisted.** They are written to the camera and
  never to the station configuration. Nothing is saved until SAVE & APPLY.
- **Stopping restores what was saved**, so leaving without saving leaves the
  camera as it was found. If that restore fails, the HMI says so explicitly and
  names the consequence, because the camera is then carrying settings that are
  not the station's.

The preview also stops on application shutdown, and refuses to start while an
inspection, a reference capture, a validation run, or an ML capture owns the
camera.

## Colour and tone controls

| Control | Values |
| --- | --- |
| White balance mode | Camera default, Off, Once, Continuous |
| Balance ratios | Per-channel red / green / blue; 0 leaves the channel untouched |
| Black level | Off, or an explicit value |
| Gamma | Off, or an explicit value |

Every field leaves the camera alone unless deliberately enabled, so a station
configured before this release is unaffected. A camera that cannot perform one
of them raises an error only when the station actually asked for it: a mono
camera has no white balance, and that is not a fault until someone tries to set
it. The capability probe reports the camera's real ranges for each.

### White balance is an inspection setting, not a preference

Until now nothing in the application set or locked white balance, so whatever
the camera did on its own -- including continuous auto white balance -- is what
the measurement inherited. That matters because the silver/brass terminal-finish
check decides on chroma: it compares a terminal crop against the one stored in
the recipe reference, using the median Lab a\* and b\* of the crop.

The comparison is differential, so a constant colour cast largely cancels. A
balance that drifts, or one the camera is choosing frame by frame, does not.

**Setting white balance requires recapturing every recipe reference.** A
reference captured under one balance and parts inspected under another is
exactly the failure mode that design has, and the HMI says so on the panel.

## Verification

381 pytest test functions, expanded by parameterization to 401 collected cases,
and four command-line smoke and installation checks pass. Nine new tests cover
the preview -- that it streams, that inspections are refused while it runs, that
it refuses to start while the camera is owned, that settings never reach the
saved configuration, that stopping restores them, and that shutdown stops it --
plus the colour settings surviving a configuration round trip and defaulting to
leaving the camera alone.

Test results from the release build environment must still be recorded
separately. Passing tests are not site acceptance.
