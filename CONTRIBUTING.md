# Contributing to Pole Position

Pole Position is a production battery-polarity inspection HMI. A change here can
alter what a station accepts or rejects, so the bar is closer to a controlled
industrial change than to a typical application patch.

Read [`README.md`](README.md) first. Its **Change-control invariants** section
lists the behaviors that must be preserved unless an approved requirement
replaces them, and its **Purpose and safety boundary** section defines what the
application does and does not claim.

## Development setup

Python 3.11 x64 is the qualified baseline; 3.12 is also tested.

```bash
python -m pip install -e ".[dev]"
```

The HMI tests build real Qt widgets on the offscreen platform. On a bare Linux
machine PySide6 needs system libraries that are not installed by pip:

```bash
sudo apt-get install -y --no-install-recommends \
  libegl1 libgl1 libdbus-1-3 libxkbcommon-x11-0 libxcb-cursor0 \
  libxcb-icccm4 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
  libxcb-shape0 libxcb-xinerama0
```

Windows and macOS need no extra packages. `tests/conftest.py` selects the
offscreen Qt platform automatically, so no display server is required.

## Before you push

Run everything CI runs:

```bash
python -m pytest
python scripts/vision_smoke_test.py
python scripts/stamp_rotation_smoke_test.py
python scripts/terminal_top_gate_smoke_test.py
python scripts/verify_source_checksums.py
python -m ruff check battery_inspector scripts tests
```

The three smoke scripts are graded regressions against bundled fixtures, not
smoke tests in the trivial sense: they assert specific classifier outcomes for
known-difficult real cycles. Treat a change in their output as a change in
inspection behavior.

### Regenerate the checksum manifest

`SHA256SUMS.txt` records every tracked file and is verified in CI. Any commit
that adds, removes, or edits a tracked file must regenerate it:

```bash
python scripts/verify_source_checksums.py --write
```

Stage your changes first — the manifest is built from `git ls-files`, so a new
file must be tracked before it can be recorded.

## Testing expectations

- **Prefer real objects over stand-ins.** The controller, the widget tree, and
  the inspection cycle are all constructible headlessly; `tests/conftest.py`
  provides the fixtures. A test that asserts on page source text cannot catch
  constructor or signal drift.
- **Both backends stay pinned to simulation** in tests, so no test can reach a
  camera or a PLC.
- **Assert the invariant, not the platform.** Where a bug only manifests on one
  operating system — an unclosed file handle, a path separator — write the test
  so it fails everywhere. See the SQLite handle tests in
  `tests/test_station_transfer.py`.
- A change to graded behavior needs a regression that fails against the previous
  code. Verify that it does.

## Dependency updates

Dependabot proposes minor and patch updates monthly, plus GitHub Actions
updates. It deliberately does **not** propose major bumps of the production
runtime — Qt, NumPy, OpenCV, ONNX Runtime, pypylon, pycomm3, or the training
stack. A new major version of any of those can change graded inspection
behavior, so raising an upper bound in `pyproject.toml` is part of a
requalification: re-run the regressions, confirm the smoke-test outcomes are
unchanged, revalidate recipes if a decision contract moved, and update
`BUILD_NOTES.md` and the release note. Do it deliberately, not by merging a
routine dependency PR.

Remember that `requirements-*.txt` mirrors the `pyproject.toml` bounds for
station installs; an accepted bump usually needs both.

## Behavior changes

Per the README's change-control section, a behavior change must update the code,
the tests, `BUILD_NOTES.md`, the appropriate release note, the README, the
version declarations, and `SHA256SUMS.txt`, and re-run the regression and
station acceptance scope it affects.

## Style

- Ruff with the project's rule set is authoritative: `python -m ruff check`.
- `except: pass` and `except: continue` are enforced by `S110`/`S112`. If
  swallowing is genuinely correct, annotate it with the reason:
  `except Exception:  # noqa: S110 - why this is safe`. Silence in a
  fail-closed application should always be a documented decision.
- Comments explain why, not what. The existing code is written that way; match
  it.

## The Windows installer

CI does not build the installer. `packaging/windows/build-installer.ps1`
requires the licensed Basler pylon Runtime Redistributable and verifies its
Authenticode signature, so the build is a controlled local procedure documented
in [`docs/WINDOWS_INSTALLER.md`](docs/WINDOWS_INSTALLER.md). CI does verify that
every build input the spec and installer reference is present.
