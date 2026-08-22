# Polarity Tool v0.19.0

## Release target

v0.19.0 removes ambiguous PLC controls and adds production recipe numbers while
preserving the v0.18.0 inspection, storage, binary result, heartbeat, and bypass
contracts.

## One authoritative PLC mode

- Settings → General no longer contains a second PLC Simulation/pycomm3 setting.
- The combobox under Settings → PLC MODE is authoritative.
- The PLC tab and Overview page no longer contain an immediate enable/make
  simulation active button.
- Simulation is activated only by selecting Simulation and applying the PLC
  configuration.
- Automatic commissioning fallback is removed. A physical PLC connection,
  polling, or heartbeat failure stays faulted and never silently activates the
  simulator.

## PLC-tag production trigger

- The Camera page displays the configured PLC Trigger tag as the production
  trigger choice.
- Software and external camera-line choices are no longer exposed to the
  technician as station trigger modes.
- Production automatic cycles are initiated only by a rising edge of the
  configured PLC Trigger BOOL.
- Overview retains the explicit RUN MANUAL INSPECTION action and simulated PLC
  test trigger when Simulation is already active.

## Stable numbered recipes

- Every recipe now contains a positive integer recipe number in addition to its
  UUID, name, part number, and revision.
- The number is visible in the recipe table, recipe detail, wizard, active recipe
  header, revision history, and recipe JSON.
- New recipes default to the next available number. A technician may change it
  during initial creation; it is locked for later revisions.
- Existing recipe databases are migrated in stable insertion order. All
  revisions of one recipe receive the same number.
- Duplicate recipe names and duplicate recipe numbers are both rejected.

## Name or integer PLC recipe selection

Settings → PLC TAGS adds **Recipe selector value**:

- **Recipe name** reads the configured tag as a Logix STRING.
- **Recipe number** reads the configured tag as SINT, INT, or DINT.

The active recipe must match the received value before a PLC trigger is
accepted. Simulation exercises the same name/number selection path.

## Compatibility and schema

- Application version: `0.19.0`
- Manifest schema: `7`
- Inspection-record schema: `7`
- Inspection engine remains
  `reference_registration_terminal_face_guard_ml_v2`.
- Existing v0.18 configuration files load safely. The removed
  `plc_fallback_to_simulation` field is ignored.
