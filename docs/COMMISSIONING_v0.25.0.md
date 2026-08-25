# v0.25.0 station commissioning checklist

This is the full commissioning sequence for a Pole Position station running
v0.25.0, from a bare workstation to a station cleared for production. It
replaces the piecemeal checklists in `COMMISSIONING_v0.7.0.md` through
`COMMISSIONING_v0.10.0.md`, which covered single features as they were added.

Work through it in order. A step that fails stops commissioning; do not carry a
deviation forward on the assumption that a later step will catch it.

Record every result. Passing tests in the repository are evidence about the
software, not about this station.

---

## 0. Before you start

Have all of the following in hand:

- [ ] The `Pole-Position-v0.25.0-Setup-x64.exe` installer and its `.sha256`
- [ ] The requirements lock produced by the same build
- [ ] The exact Basler pylon Runtime Redistributable used to build it
- [ ] The qualified production ONNX model and its JSON manifest
- [ ] The PLC tag map, route, and program revision
- [ ] At least three known-good parts and at least three known-reject parts,
      including one reversed battery and one with the wrong terminal finish
- [ ] The station's asset ID and physical location

If the model is not yet qualified, sections 1 through 6 can still be completed;
sections 7 onward cannot.

---

## 1. Verify the installer before running it

```powershell
Get-FileHash -Algorithm SHA256 .\Pole-Position-v0.25.0-Setup-x64.exe
Get-Content .\Pole-Position-v0.25.0-Setup-x64.exe.sha256
```

The two must match. If they do not, stop: the installer is not the artifact that
was built and tested.

Record the code-signing status. An unsigned installer raises a Windows
SmartScreen "Unknown Publisher" prompt; that is expected for an unsigned build
and must be recorded in the station handoff record, not clicked past silently.

---

## 2. Prepare the workstation

- [ ] Windows 10 22H2 or Windows 11, x64
- [ ] The station display set to a scale factor that leaves at least a
      1280x800 workspace. v0.25.0 scrolls a page that does not fit rather than
      overlapping it, but a station screen should not need to scroll.
- [ ] Windows Defender exclusions for the Pole Position program directory and
      `C:\ProgramData\Pole Position`. Real-time scanning has been observed to
      lock evidence files during a backup and to stall long file operations.
- [ ] Power plan set so the workstation never sleeps
- [ ] Windows Update configured so it cannot restart the station unattended

---

## 3. Install and confirm the packaged software

Run the installer as administrator. Then:

```powershell
& "C:\Program Files\Pole Position\PolePosition.exe" --verify-install
Get-Content "C:\ProgramData\Pole Position\PolePosition-install-check.json"
```

The check verifies the packaged software only. It does not look at the camera,
the PLC, the model, or the fixture.

Confirm in `BUILD-MANIFEST.json` beside the executable:

- [ ] `version` is `0.24.0`
- [ ] `git_commit` matches the commit recorded in the handoff record
- [ ] `pylon_runtime_sha256` matches the redistributable you were given
- [ ] `cuda_version` is populated if this station trains models, empty if not

A station intended for GPU training with an empty `cuda_version` received a
CPU-only bundle. Training will work and will be far slower. Decide deliberately;
do not discover it during a training run.

---

## 4. Camera

- [ ] Basler camera detected: Settings → Camera → the backend reports the model
      and serial
- [ ] Lens, working distance, aperture, and focus set and locked
- [ ] Lighting set, and any external controller set to a fixed level
- [ ] Exposure and gain set so a terminal top is neither clipped nor noisy
- [ ] The camera profile saved

```powershell
python scripts\camera_probe.py
```

Record the camera model, serial, lens, working distance, and lighting
arrangement in the handoff record. A future station has to be able to reproduce
this optical setup, and the model was trained under it.

**The backend must not be left on simulation.** A simulated camera grades a
bundled demonstration image, so every inspection reports on that image rather
than on the part in the fixture. `scripts\diagnose_station.py` reports this as
its first finding.

---

## 5. PLC

Commission the interface against the real PLC and the real program revision.
`docs/PLC_INTERFACE.md` holds the tag specification; the sequencing is described
in the operator manual for this version.

- [ ] Route and path reach the controller
- [ ] Every tag in the map exists, with the expected data type
- [ ] Heartbeat observed at both ends
- [ ] A trigger from the PLC produces exactly one inspection
- [ ] Pass and Fail arrive at the PLC as the binary result the program expects
- [ ] Result lifetime decided deliberately: the acknowledge tag is either left
      blank, so results stay latched until the next cycle, or configured against
      a program that raises the bit after consuming a result
- [ ] With the acknowledge tag configured: raising the bit clears Busy,
      Complete, Pass, and Fail together
- [ ] With the acknowledge tag configured: holding the bit high does not clear
      the next cycle's result. A stopped controller must not erase results it
      never read
- [ ] Recipe selection by number selects the recipe the PLC intends
- [ ] A recipe number the station does not have is logged and refused, and the
      PLC sees no result rather than a pass
- [ ] Disconnecting the network cable mid-cycle leaves the line safe
- [ ] The PLC, not Pole Position, owns the reject, stop, and bypass logic

Record the poll and heartbeat timings actually observed, not the configured
values.

---

## 6. Recipe

Build the recipe for the first part on the real fixture, with the real part in
place. `docs/RECIPE_EDITING.md` and the operator manual cover the wizard.

- [ ] Reference image captured from this camera, on this fixture, under this
      lighting
- [ ] Battery outline and both terminal regions taught
- [ ] Expected marking set for each terminal
- [ ] **Expected finish set for each terminal** — silver or brass, never left
      unset
- [ ] **Red-ring requirement set correctly for each terminal**
- [ ] Guided validation completed with the required number of runs
- [ ] Recipe activated

Then confirm what was actually stored:

```powershell
python scripts\diagnose_station.py --station "C:\ProgramData\Pole Position"
```

- [ ] The report is clean
- [ ] The red-ring flag printed for each terminal matches the part
- [ ] The expected finish printed for each terminal matches the part

**This confirmation is mandatory for v0.25.0.** Builds before v0.25.0 could
clear a terminal's red-ring requirement when a recipe was reopened for edit. A
cleared requirement does not appear anywhere on the inspection screen and
produces a pass on a part that should reject. Any recipe revision created or
edited before v0.25.0 must be checked this way, and a recipe with a cleared
requirement must be corrected in a new revision and revalidated.

---

## 7. Model

Only for a station grading with the ML classifier.

- [ ] The qualified ONNX and its JSON manifest installed through Settings
- [ ] `scripts\ml_model_probe.py` reports ready, with the expected classes,
      SHA-256, and input size
- [ ] The recipe's bound model SHA-256 matches the installed model
- [ ] The evaluation and challenge-set report filed with the handoff record

`scripts\diagnose_station.py` reports a recipe bound to a model other than the
one installed. A mismatch means the recipe was validated against a different
model than the one now grading parts; revalidate rather than rebind.

---

## 8. Challenge tests on the physical fixture

This is the section that qualifies the station. Nothing before it does.

Run each part through the real trigger path, not a manual button, and record
the result and the evidence reference:

- [ ] Three known-good parts pass
- [ ] A reversed battery rejects
- [ ] A part with the wrong terminal finish rejects
- [ ] A part missing a required red ring rejects
- [ ] A part rotated to each end of the physically possible range grades
      correctly
- [ ] An empty fixture does not pass
- [ ] A part removed mid-cycle does not pass
- [ ] Covering the lens does not produce a pass

Every reject must name the correct terminal. In v0.25.0 the terminal that caused
a reject is drawn in red at heavier weight on the operator view; confirm the
terminal it marks is the terminal actually at fault.

**A single unexplained pass on a part that should reject stops commissioning.**
Do not average it away across a sample; find the cause.

---

## 9. Evidence, retention, and recovery

- [ ] A reject writes evidence, and the evidence opens from the detail page
- [ ] `EXPORT INSPECTION ZIP` produces a readable archive for a reject
- [ ] A pass writes no evidence and offers no export — this is policy, not a
      fault; see `docs/STORAGE_POLICY.md`
- [ ] Retention limits set for this station's storage
- [ ] Disk health reported on the diagnostics page matches the actual volume

Rehearse recovery before you need it:

- [ ] Take a workstation backup and record its SHA-256 and size
- [ ] Restore it onto a second workstation, or the same one after a reinstall
- [ ] Confirm the restored station's recipes, models, and configuration match
- [ ] Confirm the restored active recipe still has its red-ring and finish
      settings, with `scripts\diagnose_station.py`
- [ ] Record how long the backup and the restore each took

```powershell
python scripts\analyze_station_backup.py "path\to\backup.zip"
```

Use this to confirm the backup carries what a replacement station needs and is
not dominated by regenerable training data.

---

## 10. Sign-off

Commissioning is complete when every section above is recorded, and:

- [ ] Production owner named and trained
- [ ] Quality and vision owner named
- [ ] Controls owner named
- [ ] The approved bypass procedure is written and understood
- [ ] The operator manual revision matches the application version
- [ ] Unresolved deviations listed explicitly, with the decision to accept each
      one recorded against a name

Use the handoff record template in `README.md`.

An unresolved deviation is not closed by commissioning. If it affects whether a
bad part can pass, the station does not run production.
