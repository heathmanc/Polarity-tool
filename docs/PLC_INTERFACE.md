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
| Result acknowledge (optional) | *(blank by default)* | BOOL | PLC → HMI |
| Station ready (optional) | *(blank by default)* | BOOL | HMI → PLC |

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

### Result lifetime

There is no result sequence number, and `Complete` is never cleared because
`Trigger` returned low. How long a result stays on the tags depends on whether
the optional acknowledge tag is configured.

**Acknowledge tag blank (default, and the behaviour of every station
commissioned before v0.25.0).** The completed state remains written until the
next accepted cycle publishes Busy, or until PLC settings are
connected/applied and the HMI verifies the idle row. PLC logic must consume
`Complete` as the validity of the latest result and must not wait for the HMI to
clear it after Trigger falls.

**Acknowledge tag configured.** The controller raises the acknowledge bit once
it has taken the result. On the next poll the HMI writes the idle row --
`Busy`, `Complete`, `Pass`, and `Fail` all false -- and the controller drops the
bit. `Complete` then behaves as a one-shot per cycle.

The handshake is edge-driven in both directions, and the rules that make it
safe are worth stating explicitly:

- The HMI acts on a **rising** edge of acknowledge, and the bit must be observed
  low before it can acknowledge again. A controller that stops with the bit
  held high therefore clears nothing further; it does not silently erase
  every result it never read.
- Acknowledgement can only **clear**. It never sets `Pass`.
- A result is cleared only when one is outstanding. An acknowledge with no
  published result does nothing.
- Triggering a new cycle before acknowledging the previous result is allowed --
  Busy overwrites it, as it always has -- but the HMI records an audit event
  saying the result was never taken. The station does not refuse the trigger:
  the controller owns the sequence, and stalling the line over a controller-side
  sequencing fault would be worse.

In either mode, `Trigger` must be observed false before a new rising edge can be
accepted.

Recommended acknowledge logic:

```text
IF BatteryVision.Complete AND NOT Result_Consumed THEN
    // latch Pass/Fail into the product record here
    Result_Consumed := TRUE;
    BatteryVision.Ack := TRUE;
END_IF;

IF NOT BatteryVision.Complete THEN
    BatteryVision.Ack := FALSE;
    Result_Consumed := FALSE;
END_IF;
```

Dropping the acknowledge bit when `Complete` goes low, rather than on a timer,
is what rearms the handshake for the next cycle.

On connection/application of PLC settings, the HMI writes the idle row of the
table. This both clears stale cycle outputs and verifies that all four output
tags exist and accept BOOL writes before the backend is declared usable.

v0.18 replaces the earlier `BatteryVision.FailCode` DINT with the
`BatteryVision.Fail` BOOL. When an older configuration is loaded, a tag ending
in `FailCode` is migrated to the same name ending in `Fail`; verify that BOOL
tag in **Settings → PLC TAGS** before reconnecting a production PLC.

## Station readiness contract

Optional and blank by default. When configured, the HMI publishes whether it
could accept a trigger and grade the part it gets.

Readiness answers **capability**, not the momentary state of a cycle. It stays
true while an inspection runs, because `Busy` already reports that. The PLC
permissive is:

```text
Safe_To_Trigger := BatteryVision.Ready AND NOT BatteryVision.Busy;
```

It is false when the station could not grade a part: no camera, no active
recipe or reference, an unusable model, or the camera held by a live preview,
a reference capture, a validation run, an ML capture, or a settings apply.

That last group is the reason the tag is worth configuring. Those are exactly
the states in which a trigger is silently dropped, so without readiness the PLC
learns the station was unavailable only by timing out.

The tag is written only when the value changes, plus once on connection and
once whenever PLC settings are applied. It is not a periodic signal, and after
a communication fault it freezes at its last written value like every other
output. The heartbeat, not readiness, is what proves the station is alive.

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

The PLC names the product on every trigger, and that name decides the recipe.
Nobody selects a recipe at the HMI for production: the station resolves the
received name or number to the **newest revision of that recipe whose validation
is complete**, and grades the part against it. There is no activation step and
no station-side recipe selection in the PLC path, so a mixed line needs no
operator intervention and the station can run headless. Recipe numbers remain
unchanged across revisions, so the PLC keeps naming the same number as recipes
are revised.

A resolution that fails is refused, never substituted. If the received name or
number is unknown, or the only revisions of it are drafts or retired, the
station logs the refusal and ignores that trigger edge; it does not grade the
part against anything else and does not publish a synthetic FAIL transaction.
The **Ready** tag goes false for as long as the PLC names an unrunnable product,
so a misconfigured line is visible as a state and not only as a timeout. The PLC
must keep the product inhibited and apply its site-standard timeout/fault logic
when Busy/Complete does not follow a request.

### Recipe source is a station setting, not an inference

**Settings → PLC TAGS → Recipe source** decides where a PLC-triggered cycle gets
its recipe:

| Recipe source | A PLC trigger grades against | The selector tag |
| --- | --- | --- |
| **PLC selector tag** (default) | the newest validated revision of the product the tag names | decides every trigger |
| **Station selection** | the recipe selected on the Recipes page | not read for product identity |

Under **PLC selector tag** there is no fallback. A tag that names nothing —
blank STRING, zero integer, a tag the program does not write yet, a renamed tag,
a comm fault — is refused exactly like an unknown product: no cycle, no
substitution, Ready low, one logged event. Earlier builds fell back to the HMI
selection in that case, which meant a blank tag could put a part through the
wrong recipe without anything on the line saying so.

Use **Station selection** for the bench, for simulation, and for a
single-product station whose PLC program carries no selector tag. It is a
deliberate configuration, not a degraded mode; a manual inspection from the HMI
always grades against it regardless of this setting.

## Recipe sessions and Busy

`Busy` means the station is occupied and must not be sent a part. It covers a
running inspection cycle *and* the whole time a recipe is open at the HMI for
editing or training — minutes rather than milliseconds. `Complete`, `Pass` and
`Fail` stay low for a session: a validation sample is not a production result
and never reaches the result tags.

Earlier releases published nothing for a recipe session. Readiness dropped for
the fraction of a second each validation sample was captured and came straight
back, so a controller watching `Ready AND NOT Busy` saw a station that looked
available between samples while a technician was standing at the fixture
placing parts by hand.

`Ready` is false for the whole session. A trigger that arrives anyway is
refused, logged once, and publishes nothing.

## Communication faults and reconnection

A lost connection stops the input poll and the heartbeat. The controller's
watchdog trips on the stopped heartbeat, and the station's outputs freeze at
their last written values. The heartbeat, not `Ready`, is the liveness signal.

The station reconnects on its own, retrying the **configured** backend with a
backoff from 2 s to 30 s. It never switches to Simulation. It stays faulted
until a real read of the input tags succeeds — opening a driver is not
evidence, since a driver can open against a controller that will not answer for
these tags. On recovery the poll and heartbeat resume, `Ready` is rewritten
from scratch, and a held recipe-session `Busy` is re-asserted.

The initial failure and the eventual recovery are each logged once. Individual
retry attempts are not, so a controller that is down overnight does not fill the
audit trail.

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

1. Create all tags with the expected types. The acknowledge tag is optional;
   create it only if the program raises it after consuming a result.
2. In the HMI, open **Settings → PLC TAGS**, choose name or number selection,
   and confirm the tag names. Leave **Acknowledge tag** blank to keep the
   latched behaviour.
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
13. With the acknowledge tag configured: trigger a cycle, confirm `Complete` is
    written, raise the acknowledge bit, and confirm `Busy`, `Complete`, `Pass`,
    and `Fail` all return to 0.
14. With the acknowledge tag configured: hold the acknowledge bit high, run a
    further cycle, and confirm the new result is still published. A held bit
    must not clear a result the controller has not read.
