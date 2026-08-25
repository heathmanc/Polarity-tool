# Pole Position v0.25.0

v0.25.0 adds an optional result-acknowledge handshake to the PLC interface. No
other behaviour changes. Inspection logic, storage policy, and the recipe and
model formats are unchanged, and a station upgrading from v0.24.0 keeps its
data and its existing PLC behaviour.

## Result acknowledge

Until now a published result stayed on the tags until the next accepted cycle
raised `Busy`. The controller had to treat `Complete` as the validity of
whatever it last read, because nothing told the station the result had been
taken.

A new optional tag closes that loop. When it is configured, the controller
raises the bit once it has consumed the result, and on the next poll the station
writes the idle row -- `Busy`, `Complete`, `Pass`, and `Fail` all false. The
controller drops the bit when `Complete` goes low, which rearms the handshake.
`Complete` then behaves as a one-shot per cycle.

| Purpose | Default tag | Type | Direction |
| --- | --- | --- | --- |
| Result acknowledge | *(blank)* | BOOL | PLC → HMI |

**The tag is blank by default, and blank means the handshake is off.** A station
commissioned before this release keeps the latched behaviour exactly, with no
new tag required in the controller and nothing to change at the station. Adding
the tag is a deliberate act under **Settings → PLC TAGS**, on a controller whose
program is ready to drive it.

### Rules that make the handshake safe

- The station acts on a **rising** edge of the acknowledge bit, and the bit must
  be observed low before it can acknowledge again. A controller that stops with
  the bit held high clears nothing further, so it cannot silently erase every
  result it never read. This is tested directly.
- Acknowledgement can only **clear**. It never sets `Pass`.
- An acknowledge with no result outstanding does nothing.
- Triggering a new cycle before acknowledging is allowed, and `Busy` overwrites
  the previous result as it always has. The station records an audit event
  saying the result was never taken, rather than letting it disappear silently.
  It does not refuse the trigger: the controller owns the sequence, and stalling
  the line over a controller-side sequencing fault would be worse.

### Recommended controller logic

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

## Documentation

- `docs/PLC_INTERFACE.md` describes both result lifetimes, the edge rules, and
  two added commissioning steps -- one for the normal handshake, one proving a
  held bit cannot clear an unread result.
- The station handbook's PLC section carries the same, and its cycle timing
  diagram now shows the acknowledge trace clearing the result row.

## Verification

368 pytest test functions, expanded by parameterization to 388 collected cases,
and four command-line smoke and installation checks pass. Seven new tests cover
the handshake, including the held-bit case, the
rearm after the bit falls, that an acknowledge alone can never publish a result,
and that a station with the tag left blank behaves exactly as v0.24.0 did.

Test results from the release build environment must still be recorded
separately. Passing tests are not site acceptance.
