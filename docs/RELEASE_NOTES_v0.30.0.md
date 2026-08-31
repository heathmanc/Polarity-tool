# Pole Position v0.30.0

v0.30.0 adds two station-to-station transfers that are smaller than a
workstation backup: a model package and a full recipe package. Inspection
logic, the PLC contract, storage policy, and the recipe and model formats are
unchanged.

## Two new packages

The workstation backup moves an entire station and is the right tool for
replacing a machine. It was the only tool, so the two things technicians
actually do between machines that are both staying — send a trained model to a
second station, and put a qualified recipe on a second line — meant either
moving the whole station or re-teaching from scratch.

| Transfer | Moves | Where |
| --- | --- | --- |
| Workstation backup | the entire station | Settings → STATION TRANSFER |
| **Model package** | one ONNX model and its manifest | Settings → POLARITY ML MODEL PACKAGE |
| **Recipe package** | one recipe revision with its reference, evidence, and bound model | Recipes → EXPORT ▾ |

Both are checksummed ZIPs: the manifest names every member with its SHA-256,
and an import that does not match refuses rather than installing a file that
was damaged or changed after it was written.

### Model package

**EXPORT MODEL PACKAGE** writes the ONNX and manifest this station inspects
with. **IMPORT MODEL PACKAGE** verifies one and installs it here. The controls
are on the model panel rather than in the training flow, because a station
receiving a model has no trained candidate.

Installing a model revalidates nothing. A recipe revision stays bound to the
model SHA-256 it was validated against; one bound to a different hash keeps
failing closed. Moving the model is what makes recipes bound to that hash
resolvable on the second station.

### Recipe package

**Recipes → EXPORT ▾ → Full recipe package** writes one revision with its
payload and validation records, the reference image the station captured and
accepted, and — when the revision is ML-bound and the station's installed model
is the one it is bound to — that model. If the station has since installed a
different model, the package says so and carries none.

The Recipes **IMPORT** button now accepts either a package (`.zip`) or the
older geometry template (`.json`), which is unchanged and still forces a fresh
reference capture and revalidation. The export menu names both so they are told
apart before the click rather than on the destination machine.

**A recipe package carries its validation evidence across machines as-is.**
That is the point — a qualified recipe reaches a second line without being
re-taught — and it is a deliberate decision with a cost: the evidence was
recorded on the exporting station's camera, lens, lighting, and fixture, and
nothing on the destination can confirm those match.

What the station does instead of checking:

- the import dialog names the source station, the export time, the sample
  count, and whether the bound model matches the one installed here;
- the import is a technician's explicit confirmation;
- both export and import are written to the audit log with the source station
  and the evidence counts, so provenance stays traceable;
- a package can never overwrite an existing revision — a revision is an
  immutable production record on both machines — and one whose number or name
  belongs to a different recipe here is refused.

**Import only onto a station of the same build, and run one known-good and one
known-bad part before releasing the recipe.** That run is the check that
catches a lighting or fixture difference, and it is the only one.

## Upgrade notes

- No recipe, model, configuration, or record migration.
- Nothing about existing exports changes: the geometry template behaves exactly
  as it did.
- A station missing a bound model still reports an unusable binding and refuses
  to inspect, so an imported recipe without its model cannot grade a part.
