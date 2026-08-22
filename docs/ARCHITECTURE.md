# Architecture

```text
PySide6 HMI
    |
AppController and Qt signals
    |
    +-- CameraService
    |      +-- MockCameraService
    |      +-- BaslerCameraService (pypylon)
    |
    +-- PlcService
    |      +-- MockPlcService
    |      +-- AllenBradleyPlcService (pycomm3)
    |
    +-- InspectionPipeline
    |      +-- ReferenceFeatureBatteryLocator
    |      +-- TerminalFaceValidator
    |      +-- TerminalFinishValidator (SILVER / BRASS)
    |      +-- RecipeConfiguredMarkingClassifier
    |      |      +-- OnnxMlMarkingClassifier (current four-class path)
    |      |      +-- ReferenceTemplateMarkingClassifier (legacy compatibility)
    |      |      `-- GeometricMarkingClassifier (engineering option)
    |      +-- OpenCV red-ring detector
    |      +-- memory-first result buffers
    |      +-- conditional non-PASS/validation evidence writer
    |
    +-- RecipeRepository (SQLite)
```

Blocking camera, PLC, and vision operations are submitted through Qt's global
thread pool via `ServiceTask`; the HMI receives results through Qt signals. The
UI never manufactures an inspection result independently of the pipeline.

## Authoritative inspection path

Manual, PLC Simulation, and pycomm3 triggers all call the same controller cycle.
The controller snapshots the active recipe, establishes a unique cycle ID,
acquires one fresh `CameraFrame`, and gives that frame to the pipeline. Startup
never grades an image.

```text
IDLE
  -> ACQUIRING
  -> LOCATING
  -> INSPECTING
  -> SAVING
  -> COMPLETE | NOT_READY | FAULT
```

A cycle result owns its image. Failure to acquire a new frame creates a fault
record; an earlier image is never substituted.

## Camera frame contract

`CameraFrame` carries:

- image pixels;
- application sequence and unique frame ID;
- request/capture UTC and monotonic timestamps;
- device frame ID/raw timestamp when available;
- backend and device identity;
- stale-frame discard count.

The Basler service drains queued results before acquisition. Software-trigger
mode issues a trigger after the request boundary. Free-run mode establishes a
post-drain frame boundary. The mock service rereads its source file for each
request.

## Reference and coordinate hierarchy

The immutable recipe reference is the coordinate authority:

- `Recipe.battery_roi` is normalized to the accepted reference image.
- `TerminalRecipe.search_roi` is normalized to the battery ROI.
- `TerminalRecipe.marking_roi` is normalized to the terminal search crop.

`ReferenceFeatureBatteryLocator` estimates a reference-to-current homography.
The current battery is then warped into the reference battery coordinate system
for terminal analysis. Battery, terminal, and marking polygons are also mapped
back onto the original current frame for HMI overlays and evidence.

Terminal search areas are masked out when building the pose feature model. This
prevents the plus/minus stamps and the red ring from steering the battery pose or
resolving the 180-degree direction. Orientation uses remaining case, notch,
label, and station-reference evidence.

## Registration safety gates

A configured locator must satisfy all recipe thresholds before terminal crops
are accepted:

- minimum good feature matches;
- minimum RANSAC inliers and inlier ratio;
- maximum median reprojection error;
- plausible scale range;
- convex, non-mirrored battery polygon;
- minimum battery visibility inside the frame;
- acceptable perspective skew;
- unambiguous 180-degree orientation, unless station direction is the recipe
  authority.

A configured locator that cannot find a presented battery returns the product
outcome `REJECT - BATTERY COULD NOT BE LOCATED`; it never falls back to the
reference coordinates.

## Polarity classification

`RecipeConfiguredMarkingClassifier` dispatches according to the immutable
recipe revision. The current production path is a four-class ONNX model bound
to the exact model ID, version, SHA-256, and taught-circle input contract used
during recipe validation. The model sees only the masked metal terminal face
and reports one of:

```text
PLUS
MINUS
BLANK
INVALID_MARKING
```

`INVALID_MARKING` is always a reject. Poor sharpness, excessive clipping,
ambiguous scores, low confidence, or inadequate class margin becomes the
fail-closed NO DECISION inspection state. A missing/invalid terminal face is
rejected by the physical-input gate before ML. SILVER/BRASS appearance and the
red ring are separate reference/color measurements.

Previously qualified recipe revisions can retain their reference-template,
geometric, three-class ML, or legacy `unreadable` contracts. Those compatibility
paths are selected only by the stored recipe revision; they do not silently
change when a current model is installed.

Physical terminal identity comes from recipe geometry, not from the detected
marking. This lets the decision logic distinguish a correct red ring from
reversed top stamps.

The runtime interfaces remain replaceable:

```python
class BatteryLocator:
    ready: bool
    status: str
    def readiness_issues(self, recipe: Recipe) -> list[str]: ...
    def locate(self, image: np.ndarray, recipe: Recipe) -> BatteryLocation: ...

class MarkingClassifier:
    ready: bool
    status: str
    def readiness_issues(
        self,
        recipe: Recipe,
        reference_battery: np.ndarray | None = None,
    ) -> list[str]: ...
    def classify(
        self,
        marking_crop: np.ndarray,
        terminal: TerminalRecipe,
        recipe: Recipe,
        reference_battery: np.ndarray,
    ) -> MarkingClassification: ...
```

A future YOLO OBB locator can therefore be added without changing the HMI,
recipe revision model, or PLC result contract if measured localization data
shows that it is needed.

## Guided validation and immutable templates

The recipe wizard uses the same fresh camera, registration, classification,
ring, and evidence path as production. Validation samples are bound to a SHA-256
configuration fingerprint covering the reference, ROIs, expected markings,
ring rules, locator settings, and classifier settings.

Only distinct-pose PASS samples count toward activation. Successful marking
crops are copied into the immutable recipe revision directory before the recipe
is saved, so later production-evidence cleanup cannot remove classifier
references.

## Memory and evidence contract

The camera frame, aligned/reference battery images, terminal/marking crops, and
diagnostics remain in memory throughout analysis. The result object exposes
those runtime-only buffers to Overview and Inspection Detail without including
them in serialized records.

A production PASS is finalized in memory and receives no evidence paths,
manifest, SQLite inspection row, or per-cycle audit event. A production
non-PASS is materialized only after grading: the pipeline writes the full image,
capture metadata, aligned/reference images when available, crops, diagnostics,
and manifest. Guided validation always uses the materialized path regardless of
disposition because its samples commission the recipe.

The manifest records frame identity, recipe revision, reference hash,
registration metrics, transformed polygons, class scores, ring measurements,
and final disposition. Failure retention runs only against positively identified
cycle directories under `runtime/inspections`; validation, recipes, models, and
training data are outside that traversal. Production yield counters and the
recent-result strip are session-only.

## Independent service sources

Camera and PLC backends are selected independently:

- `camera_backend = auto` selects the first available pylon device and may use an
  explicitly labeled mock fallback for commissioning.
- `camera_backend = basler` requires physical pylon hardware.
- `camera_backend = simulation` uses the demo image source.
- `plc_backend = pycomm3` selects `AllenBradleyPlcService`.
- `plc_backend = simulation` selects `MockPlcService`.

This permits physical-camera commissioning without a live PLC. Camera profiles
are capability-driven; model and serial number are displayed for verification
but are not persisted as station requirements.

## Vision readiness boundary

The pipeline is fail-closed. It emits PASS or product REJECT only when:

- a reference-backed active recipe is available;
- the immutable reference file exists;
- real validation records match the current configuration fingerprint;
- the locator and classifier report ready;
- battery registration succeeds;
- every enabled marking and ring check is evaluated.

Missing configuration produces NOT READY. Camera or internal failures produce a
SYSTEM FAULT. Neither path can become PASS.
