# PLC Interface — Binary Result, Heartbeat, and Bypass

## Default Logix tag map

The HMI uses the following default tags. All names remain editable under **Settings → PLC TAGS**.

| Purpose | Default tag | Suggested Logix type | Direction |
| --- | --- | --- | --- |
| Inspection trigger | `BatteryVision.Trigger` | BOOL | PLC → HMI |
| HMI busy | `BatteryVision.Busy` | BOOL | HMI → PLC |
| Inspection complete | `BatteryVision.Complete` | BOOL | HMI → PLC |
| Inspection pass | `BatteryVision.Pass` | BOOL | HMI → PLC |
| Inspection fail | `BatteryVision.Fail` | BOOL | HMI → PLC |
| Requested recipe | `BatteryVision.RecipeName` | STRING or SINT/INT/DINT | PLC → HMI |
| HMI heartbeat | `BatteryVision.Heartbeat` | BOOL | HMI → PLC |
| Inspection bypass request | `BatteryVision.Bypass` | BOOL | HMI → PLC (read back by HMI) |

## Binary result contract

The PLC receives only PASS or FAIL. Inspection reasons remain available in the
HMI and retained failure manifest; no reason/code value is written to Logix.

| Cycle phase | Busy | Complete | Pass | Fail |
| --- | ---: | ---: | ---: | ---: |
| Idle / result cleared | 0 | 0 | 0 | 0 |
| Inspection running | 1 | 0 | 0 | 0 |
| Completed PASS | 0 | 1 | 1 | 0 |
| Completed non-PASS | 0 | 1 | 0 | 1 |

Every completed result is mutually exclusive: exactly one of `Pass` or `Fail`
is true while `Complete` is true. REJECT, NOT READY, acquisition failure, and
internal fault all publish the same binary FAIL output.

### As-built result lifetime

v0.24.0 has no result sequence, acknowledgement tag, or automatic
complete-clear when Trigger returns low. The completed state remains written
until the next accepted cycle publishes Busy, or until PLC settings are
connected/applied and the HMI verifies the idle row. Trigger must be observed
false before a new rising edge can be accepted. PLC logic must consume
`Complete` as the validity of the latest result and must not wait for the HMI to
clear it after Trigger falls.

On connection/application of PLC settings, the HMI writes the idle row of the
table. This both clears stale cycle outputs and verifies that all four output
tags exist and accept BOOL writes before the backend is declared usable.

v0.18 replaces the earlier `BatteryVision.FailCode` DINT with the
`BatteryVision.Fail` BOOL. When an older configuration is loaded, a tag ending
in `FailCode` is migrated to the same name ending in `Fail`; verify that BOOL
tag in **Settings → PLC TAGS** before reconnecting a production PLC.

## Heartbeat contract

The heartbeat is independent of image acquisition and inspection-cycle polling. By default the HMI toggles `BatteryVision.Heartbeat` every **1000 ms**, including while a camera/vision cycle is busy.

Recommended PLC watchdog behavior:

1. Store the last observed heartbeat value.
2. Whenever the heartbeat value changes, reset a watchdog timer.
3. If no change is observed for at least **3 × the configured HMI heartbeat interval**, declare HMI communications unhealthy.
4. Do not use the heartbeat value itself as a healthy/unhealthy bit; only a periodic transition proves the HMI task is alive.

Structured-text-style example:

```text
IF BatteryVision.Heartbeat <> Last_HMI_Heartbeat THEN
    Last_HMI_Heartbeat := BatteryVision.Heartbeat;
    HMI_Watchdog_Reset := TRUE;
ELSE
    HMI_Watchdog_Reset := FALSE;
END_IF;

// Implement the site-standard timer around HMI_Watchdog_Reset.
// At the default 1000 ms heartbeat, a 3000–4000 ms timeout is recommended.
```

The HMI treats a failed heartbeat write as a PLC communication fault. It does
not switch modes automatically. Select Simulation explicitly on the PLC tab
when operating without a physical controller.

## Recipe selector contract

Every recipe has a stable positive integer recipe number as well as a unique
name. Under **Settings → PLC TAGS**, configure the Recipe selector value as one
of:

- **Recipe name** — the configured selector tag is read as a Logix STRING.
- **Recipe number** — the configured selector tag is read as SINT, INT, or DINT.

The active recipe must match the received name or number before a PLC trigger is
accepted. Recipe numbers remain unchanged across revisions.

When the requested recipe does not match, v0.24.0 logs the mismatch and ignores
that trigger edge; it does not publish a synthetic FAIL transaction. The PLC
must keep the product inhibited and apply its site-standard timeout/fault logic
when Busy/Complete does not follow a request.

## Bypass contract

The Overview page contains an amber **ENABLE BYPASS / BYPASS ACTIVE** control. Enabling it writes `BatteryVision.Bypass := TRUE` and verifies the read-back value. The state is continuously read from the PLC, so a PLC-side clear/change is reflected in the HMI.

Bypass is deliberately **not** implemented as a forced PASS:

- camera acquisition continues;
- polarity inspection continues;
- non-PASS evidence continues to be recorded; PASS remains memory-only;
- actual result tags continue to be published;
- the PLC uses `BatteryVision.Bypass` to decide whether the inspection result is allowed to interlock or stop the line.

This preserves diagnostic evidence while making the abnormal operating mode explicit. The HMI displays bypass in amber and records every HMI/PLC bypass state change in the audit event log.

Recommended PLC logic concept:

```text
Bypass_Effective := BatteryVision.Bypass AND HMI_Comm_OK;
Inspection_Interlock_OK := Bypass_Effective OR BatteryVision.Pass;
```

Conditioning bypass on the heartbeat watchdog is important: an HMI/network failure must not leave a stale TRUE bypass request effective indefinitely. On a normal application shutdown the HMI also attempts to clear the bypass tag, but the PLC watchdog remains the authoritative protection against a stale request.

Use the facility's normal permissive/fault structure rather than copying this expression directly into safety-related logic. The bypass tag is an operational quality bypass, not a safety-rated function.

## Commissioning sequence

1. Create all tags with the expected types.
2. In the HMI, open **Settings → PLC TAGS**, choose name or number selection,
   and confirm the tag names.
3. Open **Settings → PLC MODE** and set the Logix path.
4. Set the heartbeat interval (default 1000 ms).
5. Select **APPLY & TEST PYCOMM3**.
6. Confirm the heartbeat tag changes state approximately once per configured interval.
7. Confirm the PLC watchdog stays healthy while an inspection is running.
8. From Overview, enable bypass and verify `BatteryVision.Bypass` becomes TRUE in Logix.
9. Disable bypass and verify the tag becomes FALSE.
10. Trigger a known PASS and verify `Complete=1, Pass=1, Fail=0`.
11. Trigger a known non-PASS and verify `Complete=1, Pass=0, Fail=1`.
12. Trigger a cycle with bypass ON and confirm the HMI still evaluates the real result while the PLC bypasses the quality interlock.
