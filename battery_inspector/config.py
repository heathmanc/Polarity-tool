from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from battery_inspector.maintenance_passcode import default_credentials


CAMERA_BACKENDS = {"auto", "basler", "simulation"}
PLC_BACKENDS = {"pycomm3", "simulation"}
PLC_RECIPE_SELECTORS = {"name", "number"}
# Where the recipe for a PLC-triggered cycle comes from. See
# AppConfig.plc_recipe_source.
PLC_RECIPE_SOURCES = {"plc", "station"}


def _known_dataclass_values(cls: type, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown persisted keys instead of making an upgrade unbootable.

    Station configuration is long-lived and may survive several application
    releases.  Unknown keys are intentionally ignored here; configuration
    values that are safety-critical to recipe validation live in the versioned
    recipe payload rather than being silently reinterpreted.
    """

    allowed = {item.name for item in fields(cls)}
    return {key: value for key, value in payload.items() if key in allowed}


@dataclass(slots=True)
class PlcTagMap:
    trigger: str = "BatteryVision.Trigger"
    busy: str = "BatteryVision.Busy"
    complete: str = "BatteryVision.Complete"
    pass_result: str = "BatteryVision.Pass"
    fail: str = "BatteryVision.Fail"
    recipe_name: str = "BatteryVision.RecipeName"
    heartbeat: str = "BatteryVision.Heartbeat"
    bypass: str = "BatteryVision.Bypass"
    # Blank by default, and blank means the handshake is off. A station that
    # was commissioned before the acknowledge tag existed keeps its latched
    # result behaviour exactly, and adding the tag is a deliberate act on a
    # controller whose program is ready to drive it.
    acknowledge: str = ""
    # Station status, HMI -> PLC. Blank means it is not published, so a station
    # commissioned before it existed is unchanged and no controller has to
    # create a tag it does not use.
    ready: str = ""


@dataclass(slots=True)
class CameraConfig:
    """Portable acquisition profile applied to whichever camera is detected.

    The model and serial number are never stored as station requirements.
    ``selection_mode`` and ``device_id`` remain only so v0.2.0 configuration
    files can be loaded; normalization always forces first-available selection.
    """

    selection_mode: str = "first_available"  # compatibility field
    device_id: str = ""  # compatibility field; always cleared
    timeout_ms: int = 3000

    pixel_format: str = ""  # blank keeps the camera's current format
    resolution_mode: str = "CameraDefault"  # CameraDefault | Maximum | Custom
    width: int = 0
    height: int = 0
    center_roi: bool = True
    offset_x: int = 0
    offset_y: int = 0

    exposure_auto: str = "CameraDefault"  # CameraDefault | Off | Once | Continuous
    exposure_us: float = 0.0
    gain_auto: str = "CameraDefault"  # CameraDefault | Off | Once | Continuous
    gain_db: float = 0.0

    # Colour and tone. The silver/brass terminal-finish check compares the
    # chroma of a terminal crop against the recipe reference, so white balance
    # is not a cosmetic setting here: an uncontrolled or drifting balance moves
    # the measurement that decides the check. Black level and gamma move the
    # brightness distribution the same check clips against.
    #
    # Each of these leaves the camera alone unless deliberately enabled, so a
    # station configured before they existed behaves exactly as it did.
    balance_white_auto: str = "CameraDefault"  # CameraDefault | Off | Once | Continuous
    balance_ratio_red: float = 0.0  # 0 leaves the channel untouched
    balance_ratio_green: float = 0.0
    balance_ratio_blue: float = 0.0
    black_level_enabled: bool = False
    black_level: float = 0.0
    gamma_enabled: bool = False
    gamma: float = 1.0

    # Free-run cadence. Irrelevant to a triggered station: an exposure happens
    # because the station asked for one, not on a clock. It still governs a
    # free-running camera, where it sets how long a cycle waits for a frame
    # boundary, and the bandwidth and heat of a camera nobody is reading.
    frame_rate_enabled: bool = False
    frame_rate_fps: float = 10.0
    # How a frame is acquired.
    #
    # "On"  -- triggered snapshot. The station executes a software trigger and
    #          the camera exposes on demand, so the frame belongs to the cycle
    #          that asked for it and cycle latency does not depend on a frame
    #          rate. This is what a station inspecting a stopped part wants.
    # "Off" -- free run. The camera exposes continuously; a cycle drains the
    #          queue, discards one boundary frame, and grades the next
    #          completed exposure. Every station built before v0.29.0 ran this
    #          way and keeps running this way until a technician changes it.
    #
    # Only software triggering is supported: a hardware trigger would take the
    # decision of when to expose away from the station, which is where the
    # fresh-frame-per-cycle guarantee lives.
    trigger_mode: str = "On"
    trigger_source: str = "Software"

    @property
    def resolution_text(self) -> str:
        if self.resolution_mode == "Maximum":
            return "MAXIMUM ACQUISITION ROI"
        if self.resolution_mode == "Custom" and self.width > 0 and self.height > 0:
            return f"{self.width} x {self.height}"
        return "CAMERA CURRENT / DEFAULT"

    @property
    def uses_first_available(self) -> bool:
        return True

    def normalized(self) -> "CameraConfig":
        exposure_auto = (
            self.exposure_auto
            if self.exposure_auto in {"CameraDefault", "Off", "Once", "Continuous"}
            else "CameraDefault"
        )
        gain_auto = (
            self.gain_auto
            if self.gain_auto in {"CameraDefault", "Off", "Once", "Continuous"}
            else "CameraDefault"
        )
        balance_white_auto = (
            self.balance_white_auto
            if self.balance_white_auto in {"CameraDefault", "Off", "Once", "Continuous"}
            else "CameraDefault"
        )
        resolution_mode = (
            self.resolution_mode
            if self.resolution_mode in {"CameraDefault", "Maximum", "Custom"}
            else "CameraDefault"
        )
        return CameraConfig(
            # Deliberately prevent a model/serial-specific station binding.
            selection_mode="first_available",
            device_id="",
            timeout_ms=max(250, int(self.timeout_ms)),
            pixel_format=self.pixel_format.strip(),
            resolution_mode=resolution_mode,
            width=max(0, int(self.width)),
            height=max(0, int(self.height)),
            center_roi=bool(self.center_roi),
            offset_x=max(0, int(self.offset_x)),
            offset_y=max(0, int(self.offset_y)),
            exposure_auto=exposure_auto,
            exposure_us=max(0.0, float(self.exposure_us)),
            gain_auto=gain_auto,
            gain_db=float(self.gain_db),
            balance_white_auto=balance_white_auto,
            balance_ratio_red=max(0.0, float(self.balance_ratio_red)),
            balance_ratio_green=max(0.0, float(self.balance_ratio_green)),
            balance_ratio_blue=max(0.0, float(self.balance_ratio_blue)),
            black_level_enabled=bool(self.black_level_enabled),
            black_level=float(self.black_level),
            gamma_enabled=bool(self.gamma_enabled),
            gamma=max(0.01, float(self.gamma)),
            frame_rate_enabled=bool(self.frame_rate_enabled),
            frame_rate_fps=max(0.1, float(self.frame_rate_fps)),
            trigger_mode="On" if self.trigger_mode != "Off" else "Off",
            trigger_source="Software",
        )


@dataclass(slots=True)
class MlConfig:
    """Station-wide polarity-classifier deployment settings.

    The ONNX model is deliberately station configuration rather than a battery
    recipe asset. A recipe snapshots the active model identity/hash when a new
    revision is created, so replacing a model automatically makes older recipe
    validation evidence ineligible until that recipe is revalidated.
    """

    model_path: str = "models/polarity_classifier.onnx"
    manifest_path: str = "models/polarity_classifier.json"
    use_for_new_revisions: bool = True

    def normalized(self) -> "MlConfig":
        return MlConfig(
            model_path=self.model_path.strip(),
            manifest_path=self.manifest_path.strip(),
            use_for_new_revisions=bool(self.use_for_new_revisions),
        )


def ml_configuration_requires_apply(
    current: MlConfig,
    requested: MlConfig,
    *,
    user_edited: bool,
) -> bool:
    """Return whether Save & Apply should validate a new ML package.

    Settings can remain open while the ML Training page installs a candidate.
    In that case its text fields may briefly contain an older path than the live
    controller configuration.  A difference alone is therefore not evidence
    that the technician requested an ML change.  Only an actual edit in the ML
    controls may add ML validation to a global settings save.
    """

    return bool(
        user_edited
        and current.normalized() != requested.normalized()
    )


@dataclass(slots=True)
class AppConfig:
    camera_backend: str = "auto"  # auto | basler | simulation
    plc_backend: str = "simulation"  # pycomm3 | simulation
    fullscreen: bool = False
    data_directory: str = ""
    camera: CameraConfig = field(default_factory=CameraConfig)
    ml: MlConfig = field(default_factory=MlConfig)
    plc_address: str = "192.168.1.10/1"
    plc_poll_ms: int = 250
    plc_heartbeat_ms: int = 1000
    plc_recipe_selector: str = "name"  # name (Logix STRING) | number (integer)
    # Which recipe a PLC-triggered cycle grades against:
    #
    # "plc"     -- the selector tag names the product on every trigger. A read
    #              that names nothing, or names a product with no validated
    #              revision, is refused: no cycle, no substitution, Ready low.
    #              There is deliberately no fallback, because a fallback turns
    #              a blank tag into a part graded against the wrong recipe.
    # "station" -- PLC triggers grade against the recipe selected on the
    #              Recipes page, and the selector tag is not read for product
    #              identity. This is the bench and simulation path, and a
    #              legitimate production choice for a single-product station
    #              whose program does not carry a selector tag.
    plc_recipe_source: str = "plc"
    failure_retention_days: int = 30
    failure_retention_max_gb: float = 5.0
    operator_name: str = "Technician"
    # How many independent counted samples a recipe revision must pass before
    # it may be activated. Station-wide, because it describes how thoroughly
    # this site qualifies a recipe, not a property of any one battery.
    validation_runs_required: int = 5
    # Screen gating for ML Training and Settings. Stored salted and hashed so
    # the passcode is not sitting in a file in plain text, and so it can be
    # changed at the station. See maintenance_passcode.py: this is an
    # operational speed bump, not a security control.
    maintenance_passcode_salt: str = ""
    maintenance_passcode_hash: str = ""
    tags: PlcTagMap = field(default_factory=PlcTagMap)

    @property
    def simulation(self) -> bool:
        """Backward-compatible summary of the independent service backends."""

        return self.camera_backend == "simulation" and self.plc_backend == "simulation"

    @property
    def mode_text(self) -> str:
        camera_labels = {
            "auto": "CAMERA AUTO",
            "basler": "CAMERA HARDWARE",
            "simulation": "CAMERA DEMO",
        }
        plc_label = "PLC HARDWARE" if self.plc_backend == "pycomm3" else "PLC DEMO"
        return f"{camera_labels.get(self.camera_backend, 'CAMERA AUTO')} / {plc_label}"

    @classmethod
    def default(cls) -> "AppConfig":
        # Commission the first Basler camera automatically while allowing PLC work
        # to continue in simulation until the tag contract is ready.
        return cls()

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            config = cls.default()
            salt, digest = default_credentials()
            config.maintenance_passcode_salt = salt
            config.maintenance_passcode_hash = digest
            config.save(path)
            return config

        raw = json.loads(path.read_text(encoding="utf-8"))
        legacy_simulation = raw.pop("simulation", None)
        tags_raw = dict(raw.pop("tags", {}) or {})
        legacy_fail_code = str(tags_raw.pop("fail_code", "") or "").strip()
        if "fail" not in tags_raw and legacy_fail_code:
            tags_raw["fail"] = (
                legacy_fail_code[:-4]
                if legacy_fail_code.lower().endswith("code")
                else f"{legacy_fail_code}.Fail"
            )
        tags = PlcTagMap(**_known_dataclass_values(PlcTagMap, tags_raw))

        # Backward-compatible migration from first-draft flat camera fields and
        # v0.2.0 nested profiles. Legacy simulation now becomes camera AUTO plus
        # PLC simulation so run.py can commission hardware without a live PLC.
        camera_raw = raw.pop("camera", None)
        if camera_raw is None:
            raw.pop("camera_serial", None)
            legacy_timeout = int(raw.pop("camera_timeout_ms", 3000))
            camera_raw = {
                "selection_mode": "first_available",
                "device_id": "",
                "timeout_ms": legacy_timeout,
            }
        else:
            camera_raw = dict(camera_raw)
            if "resolution_mode" not in camera_raw and "use_max_resolution" in camera_raw:
                use_max = bool(camera_raw.pop("use_max_resolution"))
                camera_raw["resolution_mode"] = "Maximum" if use_max else "Custom"

        # Migrate a short-lived nested backend draft if it is encountered.
        nested_backend = str(camera_raw.pop("backend", "") or "").lower()
        camera_backend = str(raw.pop("camera_backend", "") or "").lower()
        if camera_backend not in CAMERA_BACKENDS:
            if nested_backend == "simulation":
                camera_backend = "simulation"
            elif nested_backend in {"pypylon", "basler"}:
                camera_backend = "basler"
            elif legacy_simulation is False:
                camera_backend = "basler"
            else:
                camera_backend = "auto"

        camera_raw.pop("camera_serial", None)
        camera_raw["selection_mode"] = "first_available"
        camera_raw["device_id"] = ""
        # Triggered acquisition is the default for a new station, not a change
        # applied to a commissioned one. A file written by any earlier release
        # carries trigger_mode explicitly; one that predates the field is from
        # a build that only ever ran free-running, so it keeps free run until a
        # technician changes it on the camera page.
        camera_raw.setdefault("trigger_mode", "Off")
        camera = CameraConfig(**_known_dataclass_values(CameraConfig, camera_raw)).normalized()

        ml_raw = raw.pop("ml", {})
        if not isinstance(ml_raw, dict):
            ml_raw = {}
        # Compatibility with early engineering builds that used flat paths.
        for flat_name, nested_name in (
            ("ml_model_path", "model_path"),
            ("ml_manifest_path", "manifest_path"),
            ("ml_use_for_new_revisions", "use_for_new_revisions"),
        ):
            if flat_name in raw and nested_name not in ml_raw:
                ml_raw[nested_name] = raw.pop(flat_name)
        ml = MlConfig(**_known_dataclass_values(MlConfig, ml_raw)).normalized()

        plc_backend = str(raw.pop("plc_backend", "") or "").lower()
        if plc_backend not in PLC_BACKENDS:
            plc_backend = "pycomm3" if legacy_simulation is False else "simulation"

        config = cls(
            camera_backend=camera_backend,
            plc_backend=plc_backend,
            camera=camera,
            ml=ml,
            tags=tags,
            **_known_dataclass_values(AppConfig, raw),
        ).normalized()
        # A station upgrading from a build without screen gating has no stored
        # passcode. Give it the shipped default rather than leaving the screens
        # either wide open or unreachable.
        #
        # Held in memory, not written back. Loading a configuration must never
        # modify the file: a staged restore verifies config.json against the
        # checksum in the backup manifest, and a load that rewrote the file
        # failed that check and aborted the restore. The credentials are
        # persisted the next time settings are saved; until then each launch
        # derives a fresh salt, which changes nothing for the technician
        # because the passcode itself is unchanged.
        if not config.maintenance_passcode_hash:
            salt, digest = default_credentials()
            config.maintenance_passcode_salt = salt
            config.maintenance_passcode_hash = digest
        return config

    def normalized(self) -> "AppConfig":
        camera_backend = self.camera_backend if self.camera_backend in CAMERA_BACKENDS else "auto"
        plc_backend = self.plc_backend if self.plc_backend in PLC_BACKENDS else "simulation"
        plc_recipe_selector = (
            self.plc_recipe_selector
            if self.plc_recipe_selector in PLC_RECIPE_SELECTORS
            else "name"
        )
        plc_recipe_source = (
            self.plc_recipe_source if self.plc_recipe_source in PLC_RECIPE_SOURCES else "plc"
        )
        return AppConfig(
            camera_backend=camera_backend,
            plc_backend=plc_backend,
            fullscreen=bool(self.fullscreen),
            data_directory=self.data_directory,
            camera=self.camera.normalized(),
            ml=self.ml.normalized(),
            plc_address=self.plc_address.strip(),
            plc_poll_ms=max(50, int(self.plc_poll_ms)),
            plc_heartbeat_ms=max(250, int(self.plc_heartbeat_ms)),
            plc_recipe_selector=plc_recipe_selector,
            plc_recipe_source=plc_recipe_source,
            # Zero disables that individual retention limit. Production PASS
            # images/records are never retained regardless of these settings.
            failure_retention_days=max(0, min(3650, int(self.failure_retention_days))),
            failure_retention_max_gb=max(
                0.0,
                min(10_000.0, float(self.failure_retention_max_gb)),
            ),
            operator_name=self.operator_name.strip() or "Technician",
            validation_runs_required=max(1, min(50, int(self.validation_runs_required))),
            maintenance_passcode_salt=self.maintenance_passcode_salt.strip(),
            maintenance_passcode_hash=self.maintenance_passcode_hash.strip(),
            tags=self.tags,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self.normalized())
        # These fields exist only to read older files. Omitting them from new
        # files makes it explicit that camera identity is not a station setting.
        payload["camera"].pop("selection_mode", None)
        payload["camera"].pop("device_id", None)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def resolved_data_directory(self, project_root: Path | None = None) -> Path:
        env_override = os.environ.get("BATTERY_INSPECTOR_DATA_DIR")
        if env_override:
            path = Path(env_override).expanduser()
        elif self.data_directory:
            path = Path(self.data_directory).expanduser()
        elif project_root is not None:
            path = project_root / "runtime"
        else:
            path = Path.home() / ".battery_inspector"
        path.mkdir(parents=True, exist_ok=True)
        return path


def merge_config(config: AppConfig, patch: dict[str, Any]) -> AppConfig:
    """Apply a shallow settings patch without exposing JSON details to the HMI."""

    current = asdict(config.normalized())
    for key, value in patch.items():
        if key == "tags" and isinstance(value, dict):
            current["tags"].update(value)
        elif key == "camera" and isinstance(value, dict):
            current["camera"].update(value)
        elif key == "ml" and isinstance(value, dict):
            current["ml"].update(value)
        elif key == "simulation":
            # Compatibility for integrations written against the first draft.
            current["camera_backend"] = "simulation" if bool(value) else "basler"
            current["plc_backend"] = "simulation" if bool(value) else "pycomm3"
        elif key in current:
            current[key] = value
    tags = PlcTagMap(**_known_dataclass_values(PlcTagMap, current.pop("tags")))
    camera = CameraConfig(**_known_dataclass_values(CameraConfig, current.pop("camera"))).normalized()
    ml = MlConfig(**_known_dataclass_values(MlConfig, current.pop("ml"))).normalized()
    return AppConfig(camera=camera, ml=ml, tags=tags, **current).normalized()
