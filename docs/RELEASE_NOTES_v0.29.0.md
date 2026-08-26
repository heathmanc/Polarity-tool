# Pole Position v0.29.0

v0.29.0 makes where a trigger's recipe comes from a station setting rather than
something the station infers, makes a new station acquire a true triggered
snapshot, and refuses a recipe number or name that another recipe already
claims. Inspection logic, storage policy, the result contract, and the recipe
and model formats are unchanged.

## Recipe source is configured, and a blank selector is refused

**Action required before the next run on any station upgrading from v0.28.0.**

v0.28.0 decided whether the PLC was naming the product by looking at the value
it had just read. An empty read — a blank STRING, a zero integer — meant "no
selector is configured", and the station fell back to grading the part against
whatever recipe was selected at the HMI.

That is indistinguishable from a fault. A renamed tag, a program that does not
write the selector yet, a dropped value: all of them read empty, and all of
them silently put a part through a recipe the controller never asked for. It is
the exact substitution the refusal path exists to prevent.

**Settings → PLC TAGS → Recipe source** now states it:

| Recipe source | A PLC trigger grades against | The selector tag |
| --- | --- | --- |
| **PLC selector tag** (default) | the newest validated revision of the product the tag names | decides every trigger |
| **Station selection** | the recipe selected on the Recipes page | not read for product identity |

Under **PLC selector tag** there is no fallback of any kind. A tag that names
nothing is refused exactly like an unknown product: no cycle, no substitution,
`Ready` low, one logged event naming the tag and what to check.

**Station selection** is the bench and simulation path, and a legitimate
production choice for a single-product station whose program carries no
selector tag — the case v0.28.0 could only reach by accident. A manual
inspection from the HMI grades against the station selection either way.

Stations upgrade to **PLC selector tag**. If your program does not write the
selector tag, set Recipe source to **Station selection** before the first run,
or the station will correctly refuse every trigger.

## Triggered snapshot acquisition

A cycle inspects a battery at a stop, but the station has been free-running: the
camera exposed continuously and a cycle drained the queue, discarded one frame
boundary, and graded the next completed exposure. That works, and it is why the
boundary-discard logic exists, but it makes cycle latency a function of frame
rate — up to about two frame periods, ~400 ms at 5 fps.

**Settings → CAMERA I/O → Acquisition** now offers:

- **Triggered snapshot** (default for a new station) — the station executes a
  software trigger and the camera exposes on demand. The frame belongs to the
  cycle that asked for it, and no frame rate is involved.
- **Free run** — the previous behaviour, unchanged.

Frame-rate limiting is a free-run setting and is now labelled and enabled as
one. It is greyed out under triggered acquisition, and the frame-rate cap is
written to the camera as *disabled* while triggering, because a cap throttles
how fast a triggered camera accepts triggers and would silently add latency to
a cycle.

Only software triggering is ever configured. A hardware trigger source is
normalized away: the fresh-frame-per-cycle guarantee lives in the station
deciding when to expose.

**A commissioned station keeps free run.** Every earlier release wrote the
acquisition mode into `config.json`, so an existing station reads back `Off` and
stays there until a technician changes it on the camera page. Re-test exposure
after switching — a triggered exposure is taken cold rather than mid-stream.

## One selector value names one recipe

A recipe number and a recipe name are both how the PLC names a product, so each
must identify exactly one recipe. The HMI already refused a duplicate on save;
the repository now enforces it too, so no path can create one, and the match is
case-insensitive in both directions — uniqueness and resolution now agree, and
a controller sending `group31_xhd` resolves a recipe stored as `GROUP31_XHD`.

Revisions of the same recipe share both identifiers, which is the point, and are
unaffected.

`scripts/diagnose_station.py` gained a **RECIPE SELECTION** section: the
configured source, how many products have a validated revision, and any selector
value that more than one recipe claims. A station carrying a duplicate created
before this release is reported rather than blocked, and must be resolved before
it runs those products.

## Upgrade notes

- No recipe, model, configuration, or record migration.
- Set **Recipe source** before the first run. This is the one change that can
  stop a line that previously ran on the inferred fallback.
- Re-run `scripts/diagnose_station.py` and clear any ambiguous selector value it
  reports.
- Decide **Acquisition** per station; existing stations keep free run.
- Regenerate the controls handout (`scripts/build_plc_icd.py`) and reissue it.
  The ICD is Rev C.
