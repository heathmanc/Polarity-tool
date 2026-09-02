# Pole Position v0.32.0

v0.32.0 holds `Busy` high for the whole time a recipe is open at the HMI, and
makes the station reconnect to the PLC by itself after a communication fault.
Inspection logic, storage policy, the result contract, and the recipe and model
formats are unchanged.

## Busy covers a recipe session, not just a cycle

**Controls engineers: re-read section 4.1 of the ICD.** The meaning of `Busy`
has widened.

`Busy` used to mean only "an inspection cycle is running". That left the whole
of recipe editing and validation invisible on the wire. Readiness dropped for
the fraction of a second each validation sample was captured and came straight
back, so a controller watching `Ready AND NOT Busy` saw a station that looked
available between samples — while a technician was standing at the fixture
placing parts by hand.

`Busy` now means **the station is occupied and must not be sent a part**, and it
covers two situations the controller does not need to tell apart:

| Situation | Busy | Complete / Pass / Fail | Duration |
| --- | --- | --- | --- |
| Inspection cycle | high | published when it finishes | milliseconds |
| Recipe open at the HMI | high | all stay low | minutes |

`Ready` is false for the whole session too. `Complete`, `Pass` and `Fail` stay
low throughout: a validation sample is not a production result and never
reaches the result tags.

A trigger that arrives during a session is refused and logged once — no cycle
runs, nothing is published. `Busy` is released when the recipe is closed,
including when the wizard closes on an error, and it is re-asserted if the PLC
connection drops and comes back while the recipe is still open.

## The station reconnects to the PLC on its own

A lost connection used to be terminal. Both timers stopped, the station went to
FAULT, and nothing tried again until a technician walked to the HMI and pressed
APPLY & TEST PYCOMM3. A switch reboot, a controller download, or a cable knocked
at shift change took the station out for as long as it took somebody to notice.

"Never falls back to Simulation" and "never retries" are different rules. The
station kept both; it now keeps only the first.

- Retries the **configured** backend on a backoff from 2 s to 30 s. The mode
  never changes.
- Stays faulted, with `Ready` false and the heartbeat stopped, until a real read
  of the input tags succeeds. Opening a driver is not treated as evidence — a
  driver can open against a controller that will not answer for these tags.
- On recovery: polling and the heartbeat resume, `Ready` is rewritten from
  scratch, and a held recipe-session `Busy` is re-asserted, because a controller
  that dropped and came back has no memory of what it was last told.
- The initial failure and the eventual recovery are each logged once. Retry
  attempts are not, so a controller down overnight does not fill the audit
  trail with identical rows.
- A technician pressing APPLY & TEST supersedes any pending reconnection and
  restarts the backoff.

**The fail-safe is unchanged and still yours.** While communications are down
the heartbeat is stopped, so the controller's watchdog trips and the station's
outputs freeze at their last written values. The heartbeat, not `Ready`, is the
liveness signal. Automatic reconnection shortens the outage; it does not remove
the controller's responsibility to inhibit the product during one.

Simulation is deliberately never reconnected: retrying it would hide a genuine
defect behind a retry loop.

## Upgrade notes

- No recipe, model, configuration, or record migration.
- Nothing to configure. Both behaviours are on.
- Reissue the controls handout (`scripts/build_plc_icd.py`). The ICD is Rev D
  and section 4.1 changed; the handbook is Rev F.
- If your program treats `Busy` as strictly "a cycle is in progress" and times
  out on it, that timeout must now tolerate a recipe session, or gate on
  `Ready` instead.
