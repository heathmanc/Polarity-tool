# PLC Simulation and pycomm3 Configuration

## Overview test control

The **Overview** page shows the live handshake and provides **SEND TEST PLC
TRIGGER** while Simulation is the configured PLC mode. PLC mode itself is
changed only under **Settings → PLC MODE**.

The state strip reports the heartbeat and bypass state in addition to the cycle handshake:

```text
Heartbeat | Bypass | Trigger | Busy | Complete | Result
```

The normal **RUN MANUAL INSPECTION** action remains available for technician
captures. Production automatic inspection requests come only from the
configured PLC Trigger tag.

## Select simulation from Settings

Open **Settings → PLC MODE** and select:

Select:

```text
Simulation — no physical PLC required
```

Then select **APPLY & TEST PLC**. The saved configuration is:

```json
{
  "plc_backend": "simulation"
}
```

No network connection is attempted. The header and footer deliberately show
that PLC Simulation is active. There is no commissioning fallback: if pycomm3
is selected and the physical PLC is unavailable, PLC health stays faulted.

## Exercise a simulated cycle

After simulation is active, select **SEND TEST PLC TRIGGER**. The mock PLC presents a one-shot trigger to the same polling path used by pycomm3. The controller then:

1. starts an inspection;
2. publishes `Busy` internally;
3. grades the image;
4. publishes mutually exclusive `Pass` / `Fail` BOOL outputs and `Complete` internally;
5. returns the HMI to READY with the last result visible.

A trigger received while the camera is briefly occupied is latched as one pending inspection instead of being discarded.

## Switch to a Logix PLC

Under **Settings → PLC MODE**, choose:

```text
pycomm3 — Allen-Bradley Logix PLC
```

Enter the Logix path and poll interval. Under **PLC TAGS**, choose whether the
recipe selector is a STRING name or an integer number, then confirm the tag map
and select **APPLY & TEST PYCOMM3**. The replacement connection is opened and a
cycle-state read is verified before it becomes active. If verification fails,
the existing backend is retained and a PLC fault is reported; the application
never switches to Simulation automatically.

## Default tags

```text
BatteryVision.Trigger
BatteryVision.Busy
BatteryVision.Complete
BatteryVision.Pass
BatteryVision.Fail
BatteryVision.RecipeName  (configurable STRING name or integer recipe number)
BatteryVision.Heartbeat
BatteryVision.Bypass
```

The HMI heartbeat toggles independently of inspection activity; see [`PLC_INTERFACE.md`](PLC_INTERFACE.md) for the watchdog and bypass contracts.

These remain editable commissioning defaults. The simulator uses the same binary
result behavior as pycomm3: both result bits are clear while Busy is true, then
exactly one result bit is true when Complete is published.
