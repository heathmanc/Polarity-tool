# Polarity Tool v0.16.0

## PLC heartbeat and bypass

This release adds the two PLC-operability features requested during station commissioning.

### Heartbeat

- New configurable heartbeat interval, default 1000 ms.
- `BatteryVision.Heartbeat` is toggled by a dedicated HMI timer rather than by the normal PLC poll.
- The heartbeat continues while an inspection is ACQUIRING / LOCATING / INSPECTING / SAVING.
- Heartbeat write failure is treated as a PLC communication fault and follows the existing fallback policy.
- Settings and Diagnostics show heartbeat interval/state.

Recommended PLC watchdog: declare HMI communications unhealthy if the heartbeat does not change within approximately 3x the configured interval.

### Bypass

- New default BOOL tag: `BatteryVision.Bypass`.
- Overview has a persistent bypass button.
- Enabling bypass requires confirmation and is displayed in amber.
- HMI write is verified by PLC read-back.
- PLC-side bypass changes are read back and displayed.
- All changes are audit logged.
- Bypass does not force PASS or disable inspection; the HMI continues to inspect and record actual results while the PLC uses the bypass tag to bypass the quality interlock.

### PLC simulation

The internal PLC simulator implements the same heartbeat and bypass state so the complete HMI behavior can be commissioned without a physical controller.

See `docs/PLC_INTERFACE.md` for the recommended tag types and ladder/watchdog contract.

### Recipe compatibility

The inspection engine and evidence schema are unchanged from v0.15.0. Existing v0.15 recipe validation remains valid.
