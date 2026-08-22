# Production Storage Policy

## Production cycles

Pole Position evaluates every production camera frame directly from RAM.
The full frame, registered battery image, terminal crops, marking crops, and
diagnostic images remain in memory long enough to grade the part and render the
latest result in the HMI.

- **PASS:** no production image, manifest, SQLite inspection row, or per-cycle
  audit event is written. The in-memory buffers are released when the result is
  replaced or the application exits.
- **Non-PASS:** REJECT, NOT READY, acquisition failure, and SYSTEM FAULT receive
  a complete evidence directory and inspection record. The HMI reason remains
  richer than the binary PLC FAIL output.

The top-bar counts and recent-result strip are session-only and reset when the
application starts. This avoids recreating persistent PASS history through an
aggregate counter.

## Failure retention

Failure retention is configured under **Settings → General**:

| Setting | Default | Behavior |
| --- | ---: | --- |
| Failure retention age | 30 days | Removes non-PASS cycles older than the limit |
| Failure storage limit | 5.0 GB | Removes the oldest cycles until usage is within the limit |

Set an individual limit to `0 / Disabled` to disable that limit. When both are
enabled, a failure is removed when the age sweep expires it or when the capacity
sweep needs space. The capacity sweep preserves the newest failure package even
if that single package exceeds the configured limit.

Retention operates only on positively identified cycle directories under:

```text
runtime/inspections/YYYYMMDD/<cycle-id>/
```

It does not traverse or delete validation captures, immutable recipe references,
validation templates, ML training samples, installed models, configuration, or
audit configuration.

## Upgrade behavior

At v0.18 startup, legacy production PASS evidence directories, PASS inspection
rows, and PASS per-cycle audit events from earlier releases are removed so the
new policy is true immediately. Guided recipe-validation PASS evidence is kept.
Unknown or incomplete evidence directories without a readable disposition are
left untouched for manual maintenance review.
