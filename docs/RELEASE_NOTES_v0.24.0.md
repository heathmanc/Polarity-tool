# Pole Position v0.24.0

v0.24.0 corrects two defects that could affect what a station inspects, makes
the reason for a reject visible on the operator screen, and repairs the Windows
build so that a GPU workstation gets the GPU build it asked for.

Inspection logic, the PLC contract, storage policy, and the recipe and model
formats are unchanged. A station upgrading from v0.23.4 keeps its data.

## Inspection integrity

- **The recipe editor no longer erases the checks it just loaded.** Opening a
  saved recipe for edit cleared the negative terminal's expected finish and its
  red-ring requirement, while the positive terminal appeared correct. The stored
  recipe was intact throughout; the polarity step overwrote its own copy while
  filling in the controls, because setting one control notified a handler that
  wrote the remaining controls' construction defaults back over the loaded
  values. Saving from that state was refused with `Select SILVER or BRASS`, for
  a recipe that had been saved with a finish selected.

  **Action required.** Any recipe revision saved through an affected build may
  have `red_ring_required` cleared on a terminal that requires a red ring. A
  cleared requirement is not visible on the inspection screen and produces a
  pass where the part should reject. Confirm every active recipe with
  `scripts/diagnose_station.py`, which prints the flag for each terminal, before
  returning a station to production.

- **A reject now says which terminal caused it.** The failing terminal's region
  is drawn in the reject colour at double line weight and labelled `REJECT`, on
  both the operator view and the terminal detail view. A terminal that passed
  keeps its role colour. Previously a reject was drawn identically to a pass, so
  the operator had to open the detail page to learn which terminal failed.

## Operator interface

- **Pages no longer compress themselves into overlap.** The station window's
  minimum height is shorter than the tallest page requires, so pages were laid
  out compressed before any monitor was involved, and Windows display scaling
  widened the gap -- a 4K panel at 150% reports a 1280x720 workspace. A layout
  denied room does not refuse it; it shrinks its children below the size each
  asked for, and wrapped text drew over the controls beneath it. This was
  reported on the ML training capture and review steps and affected every page.
  Each page is now placed behind a scroll view, so a shortfall scrolls instead
  of overlapping. Nothing appears when the page fits.

- **Review thumbnails keep one shape.** Sample metadata wrapped to a varying
  number of lines, so cards differed in height and the controls under them
  moved. It is now two fixed lines with the full detail in the tooltip, and
  empty cells on the last page keep their geometry instead of resizing the cards
  beside them.

- **The machine-state banner is no longer clipped.** It was capped four pixels
  below the height of its own text on every screen.

## Windows build and installer

- **A CUDA PyTorch build is no longer replaced by the CPU one.** Both build
  scripts installed the requested CUDA wheel and then installed the requirements
  over the top with `--upgrade`, which takes a directly named requirement to the
  newest version its index offers even when the installed version already
  satisfies the range. PyPI's newest Windows wheel is CPU-only and satisfies
  `torch>=2.2`, so the CUDA build was uninstalled and a station with an NVIDIA
  card received a CPU-only bundle. The resolved versions are now pinned through
  every later install, and requesting a CUDA index but resolving a CPU build
  fails the release instead of warning.

- **What kind of PyTorch was bundled is read from `torch.version.cuda`**, a
  property of the build, rather than from whether the build machine happens to
  have a driver. A build machine without a GPU can produce a valid CUDA bundle.
  Both build manifests record the outcome.

- **Locked files no longer abort a finished build.** Windows refuses to delete
  an executable image a running process has mapped, so a previous build left
  open failed the collect step after the freeze had already succeeded. Deletions
  now clear read-only attributes, retry momentary antivirus and indexer holds,
  and on a persistent hold name the process running out of the directory.

- **The installer compresses across four threads.** A solid LZMA2 stream over a
  bundle carrying the CUDA training runtime ran single-threaded for hours while
  printing nothing.

- **The PyTorch probe runs from a file.** Passed inline after `-c`, PowerShell
  stripped its quotes and the interpreter reported a syntax error, which the
  build reported as PyTorch being unimportable.

## Other corrections

- Training in a packaged build no longer fails with
  `'NoneType' object has no attribute 'write'`. A windowed PyInstaller build has
  no standard output, and the training subprocess wrote to it.
- The settings page bound spin boxes over `self.width` and `self.height`, which
  are `QWidget` methods; any call to `width()` or `height()` on that page raised.

## Verification

380 pytest test functions and four command-line smoke and installation checks
pass on Linux and Windows. New coverage in this release measures page layout
deficits directly at four workspace sizes, walks a recipe through save, reopen,
and re-save while reading the stored record at each step, and asserts the reject
overlay convention in both directions.

Test results from the release build environment must still be recorded
separately. Passing tests are not site acceptance.
