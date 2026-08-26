from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import monotonic_ns
from typing import Any, Iterable
from uuid import uuid4

import cv2
import numpy as np

from battery_inspector.config import CameraConfig


class CameraError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class CameraDeviceInfo:
    index: int
    model_name: str = "Unknown camera"
    serial_number: str = ""
    friendly_name: str = ""
    user_defined_name: str = ""
    device_class: str = ""
    transport: str = ""

    @property
    def display_name(self) -> str:
        name = self.user_defined_name or self.friendly_name or self.model_name
        transport = f" | {self.transport}" if self.transport else ""
        serial = f" | S/N {self.serial_number}" if self.serial_number else ""
        return f"{name}{transport}{serial}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class NumericCapability:
    name: str
    available: bool = False
    writable: bool = False
    minimum: float = 0.0
    maximum: float = 0.0
    increment: float = 0.0
    current: float = 0.0
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class CameraCapabilities:
    device: CameraDeviceInfo | None = None
    sensor_width_px: int = 0
    sensor_height_px: int = 0
    width: NumericCapability = field(default_factory=lambda: NumericCapability("Width", unit="px"))
    height: NumericCapability = field(default_factory=lambda: NumericCapability("Height", unit="px"))
    offset_x: NumericCapability = field(default_factory=lambda: NumericCapability("Offset X", unit="px"))
    offset_y: NumericCapability = field(default_factory=lambda: NumericCapability("Offset Y", unit="px"))
    exposure_us: NumericCapability = field(default_factory=lambda: NumericCapability("Exposure", unit="us"))
    gain_db: NumericCapability = field(default_factory=lambda: NumericCapability("Gain", unit="dB"))
    frame_rate_hz: NumericCapability = field(default_factory=lambda: NumericCapability("Frame rate", unit="Hz"))
    pixel_formats: tuple[str, ...] = ()
    current_pixel_format: str = ""
    exposure_auto_modes: tuple[str, ...] = ()
    current_exposure_auto: str = "Off"
    gain_auto_modes: tuple[str, ...] = ()
    current_gain_auto: str = "Off"
    balance_ratio: NumericCapability = field(
        default_factory=lambda: NumericCapability("White balance ratio")
    )
    balance_white_auto_modes: tuple[str, ...] = ()
    current_balance_white_auto: str = "Off"
    balance_ratio_selectors: tuple[str, ...] = ()
    black_level: NumericCapability = field(
        default_factory=lambda: NumericCapability("Black level")
    )
    gamma: NumericCapability = field(default_factory=lambda: NumericCapability("Gamma"))
    frame_rate_enable_available: bool = False
    frame_rate_enabled: bool = False
    trigger_modes: tuple[str, ...] = ()
    current_trigger_mode: str = "Off"
    trigger_sources: tuple[str, ...] = ()
    current_trigger_source: str = ""

    @property
    def maximum_resolution(self) -> tuple[int, int]:
        width = self.sensor_width_px or int(round(self.width.maximum))
        height = self.sensor_height_px or int(round(self.height.maximum))
        return width, height

    @property
    def maximum_acquisition_resolution(self) -> tuple[int, int]:
        return int(round(self.width.maximum)), int(round(self.height.maximum))

    @property
    def active_resolution(self) -> tuple[int, int]:
        return int(round(self.width.current)), int(round(self.height.current))

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device.to_dict() if self.device else None,
            "sensor_width_px": self.sensor_width_px,
            "sensor_height_px": self.sensor_height_px,
            "width": self.width.to_dict(),
            "height": self.height.to_dict(),
            "offset_x": self.offset_x.to_dict(),
            "offset_y": self.offset_y.to_dict(),
            "exposure_us": self.exposure_us.to_dict(),
            "gain_db": self.gain_db.to_dict(),
            "frame_rate_hz": self.frame_rate_hz.to_dict(),
            "pixel_formats": list(self.pixel_formats),
            "current_pixel_format": self.current_pixel_format,
            "exposure_auto_modes": list(self.exposure_auto_modes),
            "current_exposure_auto": self.current_exposure_auto,
            "gain_auto_modes": list(self.gain_auto_modes),
            "balance_ratio": self.balance_ratio.to_dict(),
            "balance_white_auto_modes": list(self.balance_white_auto_modes),
            "current_balance_white_auto": self.current_balance_white_auto,
            "balance_ratio_selectors": list(self.balance_ratio_selectors),
            "black_level": self.black_level.to_dict(),
            "gamma": self.gamma.to_dict(),
            "current_gain_auto": self.current_gain_auto,
            "frame_rate_enable_available": self.frame_rate_enable_available,
            "frame_rate_enabled": self.frame_rate_enabled,
            "trigger_modes": list(self.trigger_modes),
            "current_trigger_mode": self.current_trigger_mode,
            "trigger_sources": list(self.trigger_sources),
            "current_trigger_source": self.current_trigger_source,
        }


@dataclass(slots=True, frozen=True)
class CameraState:
    connected: bool
    device: CameraDeviceInfo
    capabilities: CameraCapabilities
    settings: CameraConfig

    @property
    def description(self) -> str:
        return self.device.display_name


@dataclass(slots=True, frozen=True)
class CameraFrame:
    """One acquired image and metadata proving which cycle owns it."""

    image: np.ndarray = field(repr=False, compare=False)
    sequence: int
    frame_id: str
    requested_at_utc: str
    captured_at_utc: str
    request_monotonic_ns: int
    captured_monotonic_ns: int
    camera_frame_id: str = ""
    camera_timestamp_raw: int | None = None
    device: CameraDeviceInfo | None = None
    backend_name: str = ""
    stale_frames_discarded: int = 0

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def channels(self) -> int:
        return int(self.image.shape[2]) if self.image.ndim == 3 else 1

    @property
    def fresh(self) -> bool:
        return bool(
            self.sequence > 0
            and self.image.size
            and self.captured_monotonic_ns >= self.request_monotonic_ns
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "frame_id": self.frame_id,
            "requested_at_utc": self.requested_at_utc,
            "captured_at_utc": self.captured_at_utc,
            "request_monotonic_ns": self.request_monotonic_ns,
            "captured_monotonic_ns": self.captured_monotonic_ns,
            "camera_frame_id": self.camera_frame_id,
            "camera_timestamp_raw": self.camera_timestamp_raw,
            "width": self.width,
            "height": self.height,
            "channels": self.channels,
            "device": self.device.to_dict() if self.device else None,
            "backend_name": self.backend_name,
            "stale_frames_discarded": self.stale_frames_discarded,
            "fresh": self.fresh,
        }


class CameraService(ABC):
    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def capture(self) -> CameraFrame:
        """Acquire one frame generated after this request began."""
        raise NotImplementedError

    def grab(self) -> np.ndarray:
        """Compatibility wrapper used by camera setup/test views."""
        return self.capture().image

    @abstractmethod
    def discover_devices(self) -> list[CameraDeviceInfo]:
        raise NotImplementedError

    @abstractmethod
    def state(self) -> CameraState:
        raise NotImplementedError

    @abstractmethod
    def apply_configuration(self, settings: CameraConfig) -> CameraState:
        raise NotImplementedError

    # Compatibility names used by the controller/UI. These delegate to the
    # clearer service API and keep third-party camera adapters simple.
    def enumerate_devices(self) -> list[CameraDeviceInfo]:
        return self.discover_devices()

    def probe_capabilities(self) -> CameraCapabilities:
        return self.state().capabilities

    def apply_settings(self, settings: CameraConfig) -> CameraCapabilities:
        return self.apply_configuration(settings).capabilities

    @property
    @abstractmethod
    def connected(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def capabilities(self) -> CameraCapabilities | None:
        raise NotImplementedError

    @property
    def backend_name(self) -> str:
        """Human-readable adapter identity for the diagnostics HMI."""

        return type(self).__name__

    @property
    def is_simulated(self) -> bool:
        return False


def align_numeric(
    value: float,
    minimum: float,
    maximum: float,
    increment: float,
    *,
    integer: bool = False,
) -> float | int:
    """Clamp and align a requested GenICam value to the advertised increment."""

    if maximum < minimum:
        minimum, maximum = maximum, minimum
    clipped = min(max(float(value), float(minimum)), float(maximum))
    if increment and increment > 0:
        steps = round((clipped - minimum) / increment)
        clipped = minimum + steps * increment
        clipped = min(max(clipped, minimum), maximum)
    return int(round(clipped)) if integer else float(clipped)


class MockCameraService(CameraService):
    def __init__(self, image_path: Path, settings: CameraConfig | None = None) -> None:
        self.image_path = image_path
        self.settings = _copy_settings(settings or CameraConfig()).normalized()
        self._connected = False
        self._image: np.ndarray | None = None
        self._capabilities: CameraCapabilities | None = None
        self._capture_sequence = 0
        self._device = CameraDeviceInfo(
            index=0,
            model_name="Simulation image source",
            serial_number="SIM-0001",
            friendly_name="Mock Basler camera",
            device_class="Simulation",
            transport="File",
        )

    def connect(self) -> None:
        image = cv2.imread(str(self.image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise CameraError(f"Demo image could not be opened: {self.image_path}")
        self._image = image
        self._connected = True
        self._capabilities = self._build_capabilities()

    def disconnect(self) -> None:
        self._connected = False
        self._image = None
        self._capabilities = None

    def capture(self) -> CameraFrame:
        requested_ns = monotonic_ns()
        requested_at = datetime.now(timezone.utc).isoformat()
        if not self._connected:
            raise CameraError("Mock camera is not connected")

        # Re-read the source for every trigger. Replacing the simulation file must
        # be visible on the very next cycle rather than grading a startup cache.
        image = cv2.imread(str(self.image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise CameraError(f"Demo image could not be opened: {self.image_path}")
        self._image = image
        self._capabilities = self._build_capabilities()
        width, height = self._capabilities.active_resolution
        x = int(round(self._capabilities.offset_x.current))
        y = int(round(self._capabilities.offset_y.current))
        crop = image[y : y + height, x : x + width]
        if crop.size == 0:
            raise CameraError("Configured simulation image ROI is empty")

        self._capture_sequence += 1
        captured_ns = monotonic_ns()
        return CameraFrame(
            image=crop.copy(),
            sequence=self._capture_sequence,
            frame_id=f"SIM-{self._capture_sequence:08d}-{uuid4().hex[:8]}",
            requested_at_utc=requested_at,
            captured_at_utc=datetime.now(timezone.utc).isoformat(),
            request_monotonic_ns=requested_ns,
            captured_monotonic_ns=captured_ns,
            camera_frame_id=str(self._capture_sequence),
            camera_timestamp_raw=captured_ns,
            device=self._device,
            backend_name=self.backend_name,
        )

    def discover_devices(self) -> list[CameraDeviceInfo]:
        return [self._device]

    def state(self) -> CameraState:
        if self._image is None:
            image = cv2.imread(str(self.image_path), cv2.IMREAD_COLOR)
            if image is None:
                raise CameraError(f"Demo image could not be opened: {self.image_path}")
            self._image = image
        self._capabilities = self._build_capabilities()
        effective = _effective_settings(self.settings, self._capabilities)
        return CameraState(self._connected, self._device, self._capabilities, effective)

    def apply_configuration(self, settings: CameraConfig) -> CameraState:
        self.settings = _copy_settings(settings).normalized()
        if self._image is None:
            image = cv2.imread(str(self.image_path), cv2.IMREAD_COLOR)
            if image is None:
                raise CameraError(f"Demo image could not be opened: {self.image_path}")
            self._image = image
        self._capabilities = self._build_capabilities()
        return self.state()

    def _build_capabilities(self) -> CameraCapabilities:
        if self._image is None:
            raise CameraError("Mock camera image is not loaded")
        max_height, max_width = self._image.shape[:2]
        if self.settings.resolution_mode in {"CameraDefault", "Maximum"}:
            # The demo image represents both the current/default and maximum frame.
            width, height = max_width, max_height
            offset_x, offset_y = 0, 0
        else:
            width = int(align_numeric(self.settings.width, 64, max_width, 2, integer=True))
            height = int(align_numeric(self.settings.height, 64, max_height, 2, integer=True))
            if self.settings.center_roi:
                offset_x = int(align_numeric((max_width - width) / 2, 0, max_width - width, 2, integer=True))
                offset_y = int(align_numeric((max_height - height) / 2, 0, max_height - height, 2, integer=True))
            else:
                offset_x = int(align_numeric(self.settings.offset_x, 0, max_width - width, 2, integer=True))
                offset_y = int(align_numeric(self.settings.offset_y, 0, max_height - height, 2, integer=True))

        exposure = self.settings.exposure_us or 5000.0
        exposure = float(align_numeric(exposure, 20.0, 1_000_000.0, 1.0))
        gain = float(align_numeric(self.settings.gain_db, 0.0, 24.0, 0.1))
        frame_rate = float(align_numeric(self.settings.frame_rate_fps, 0.1, 60.0, 0.1))
        exposure_auto = _normalized_auto_mode(self.settings.exposure_auto, allow_default=True)
        gain_auto = _normalized_auto_mode(self.settings.gain_auto, allow_default=True)
        if exposure_auto == "CameraDefault":
            exposure_auto = "Off"
        if gain_auto == "CameraDefault":
            gain_auto = "Off"
        pixel_format = self.settings.pixel_format or "BGR8"
        trigger_source = self.settings.trigger_source or "Software"
        return CameraCapabilities(
            device=self._device,
            sensor_width_px=max_width,
            sensor_height_px=max_height,
            width=NumericCapability("Width", True, True, 64, max_width, 2, width, "px"),
            height=NumericCapability("Height", True, True, 64, max_height, 2, height, "px"),
            offset_x=NumericCapability("Offset X", True, True, 0, max(0, max_width - width), 2, offset_x, "px"),
            offset_y=NumericCapability("Offset Y", True, True, 0, max(0, max_height - height), 2, offset_y, "px"),
            exposure_us=NumericCapability("Exposure", True, True, 20, 1_000_000, 1, exposure, "us"),
            gain_db=NumericCapability("Gain", True, True, 0, 24, 0.1, gain, "dB"),
            frame_rate_hz=NumericCapability("Frame rate", True, True, 0.1, 60, 0.1, frame_rate, "Hz"),
            pixel_formats=("BGR8", "RGB8", "Mono8"),
            current_pixel_format=pixel_format,
            exposure_auto_modes=("Off", "Once", "Continuous"),
            current_exposure_auto=exposure_auto,
            gain_auto_modes=("Off", "Once", "Continuous"),
            current_gain_auto=gain_auto,
            frame_rate_enable_available=True,
            frame_rate_enabled=self.settings.frame_rate_enabled,
            trigger_modes=("Off", "On"),
            current_trigger_mode=self.settings.trigger_mode,
            trigger_sources=("Software", "Line1"),
            current_trigger_source=trigger_source,
        )

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def description(self) -> str:
        return f"Mock camera ({self.image_path.name})"

    @property
    def capabilities(self) -> CameraCapabilities | None:
        return self._capabilities

    @property
    def backend_name(self) -> str:
        return "MockCameraService"

    @property
    def is_simulated(self) -> bool:
        return True


class BaslerCameraService(CameraService):
    """pypylon adapter with automatic first-device selection and capability discovery."""

    def __init__(self, settings: CameraConfig | None = None) -> None:
        self.settings = _copy_settings(settings or CameraConfig()).normalized()
        self._camera: Any | None = None
        self._pylon: Any | None = None
        self._converter: Any | None = None
        self._lock = RLock()
        self._device: CameraDeviceInfo | None = None
        self._capabilities: CameraCapabilities | None = None
        self._capture_sequence = 0
        self._last_camera_frame_id = ""

    def connect(self) -> None:
        pylon = self._load_pylon()
        with self._lock:
            if self.connected:
                return
            factory = pylon.TlFactory.GetInstance()
            devices = list(factory.EnumerateDevices())
            if not devices:
                raise CameraError("No Basler camera was detected by pylon")

            selected_index, selected = _select_device(devices, self.settings)
            camera = pylon.InstantCamera(factory.CreateDevice(selected))
            try:
                camera.Open()
                self._pylon = pylon
                self._camera = camera
                self._device = _device_info(selected, selected_index)
                self._apply_settings_locked(self.settings)

                # GenICam geometry nodes such as Width and Height commonly report
                # themselves as non-writable while acquisition is running. Probe the
                # camera while idle so the HMI sees the real configurable limits for
                # whatever Basler model is attached to this station.
                self._capabilities = self._read_capabilities_locked()

                converter = pylon.ImageFormatConverter()
                converter.OutputPixelFormat = pylon.PixelType_BGR8packed
                converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
                self._converter = converter
                camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
            except Exception as exc:
                self._safe_close_locked()
                if isinstance(exc, CameraError):
                    raise
                raise CameraError(f"Unable to open/configure the Basler camera: {exc}") from exc

    def disconnect(self) -> None:
        with self._lock:
            self._safe_close_locked()

    def _safe_close_locked(self) -> None:
        if self._camera is not None:
            try:
                if self._camera.IsGrabbing():
                    self._camera.StopGrabbing()
            except Exception:  # noqa: S110 - closing an already-faulted grab must not mask the real error
                pass
            try:
                if self._camera.IsOpen():
                    self._camera.Close()
            except Exception:  # noqa: S110 - closing an already-faulted camera must not mask the real error
                pass
        self._camera = None
        self._converter = None
        self._pylon = None
        self._capabilities = None
        self._device = None
        self._last_camera_frame_id = ""

    def capture(self) -> CameraFrame:
        requested_ns = monotonic_ns()
        requested_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if self._camera is None or self._pylon is None or self._converter is None:
                raise CameraError("Basler camera is not connected")

            discarded = self._drain_available_results_locked()
            software_trigger = (
                self.settings.trigger_mode == "On"
                and self.settings.trigger_source == "Software"
            )
            free_run = self.settings.trigger_mode == "Off"

            if software_trigger:
                try:
                    self._camera.WaitForFrameTriggerReady(
                        self.settings.timeout_ms,
                        self._pylon.TimeoutHandling_ThrowException,
                    )
                    self._camera.ExecuteSoftwareTrigger()
                except Exception as exc:
                    raise CameraError(f"Basler software trigger failed: {exc}") from exc
            elif free_run:
                # A free-running exposure may already be in flight after draining
                # the completed queue. Discard one boundary frame, then grade the
                # next completed exposure. The inspected battery is stopped.
                guard = self._retrieve_result_locked(
                    "Timed out while establishing a fresh Basler frame boundary"
                )
                try:
                    self._assert_grab_succeeded(guard, "Basler boundary frame failed")
                    discarded += 1
                finally:
                    guard.Release()

            result = self._retrieve_result_locked(
                "Timed out or failed while waiting for a fresh Basler frame"
            )
            try:
                self._assert_grab_succeeded(result, "Basler grab failed")
                converted = self._converter.Convert(result)
                image = converted.GetArray().copy()
                camera_frame_id = _grab_result_identifier(result)
                camera_timestamp = _grab_result_timestamp(result)
            finally:
                result.Release()

            if camera_frame_id and camera_frame_id == self._last_camera_frame_id:
                raise CameraError(
                    "Basler returned the same device frame identifier twice; stale image rejected"
                )
            if camera_frame_id:
                self._last_camera_frame_id = camera_frame_id
            self._capture_sequence += 1
            captured_ns = monotonic_ns()
            return CameraFrame(
                image=image,
                sequence=self._capture_sequence,
                frame_id=f"CAM-{self._capture_sequence:08d}-{uuid4().hex[:8]}",
                requested_at_utc=requested_at,
                captured_at_utc=datetime.now(timezone.utc).isoformat(),
                request_monotonic_ns=requested_ns,
                captured_monotonic_ns=captured_ns,
                camera_frame_id=camera_frame_id,
                camera_timestamp_raw=camera_timestamp,
                device=self._device,
                backend_name=self.backend_name,
                stale_frames_discarded=discarded,
            )

    def _drain_available_results_locked(self, maximum: int = 32) -> int:
        if self._camera is None or self._pylon is None:
            return 0
        timeout_return = getattr(self._pylon, "TimeoutHandling_Return", None)
        if timeout_return is None:
            return 0
        discarded = 0
        for _ in range(maximum):
            try:
                result = self._camera.RetrieveResult(0, timeout_return)
            except Exception:
                break
            if result is None:
                break
            try:
                valid = getattr(result, "IsValid", None)
                if callable(valid) and not valid():
                    break
                discarded += 1
            finally:
                try:
                    result.Release()
                except Exception:  # noqa: S110 - releasing a discarded grab result is best-effort
                    pass
        return discarded

    def _retrieve_result_locked(self, context: str) -> Any:
        if self._camera is None or self._pylon is None:
            raise CameraError("Basler camera is not connected")
        try:
            return self._camera.RetrieveResult(
                self.settings.timeout_ms,
                self._pylon.TimeoutHandling_ThrowException,
            )
        except Exception as exc:
            raise CameraError(f"{context}: {exc}") from exc

    @staticmethod
    def _assert_grab_succeeded(result: Any, context: str) -> None:
        if result.GrabSucceeded():
            return
        raise CameraError(
            f"{context}: code={result.ErrorCode}, description={result.ErrorDescription}"
        )

    def discover_devices(self) -> list[CameraDeviceInfo]:
        pylon = self._load_pylon()
        factory = pylon.TlFactory.GetInstance()
        try:
            devices = list(factory.EnumerateDevices())
        except Exception as exc:
            raise CameraError(f"Basler device discovery failed: {exc}") from exc
        return [_device_info(device, index) for index, device in enumerate(devices)]

    def state(self) -> CameraState:
        pylon = self._load_pylon()
        with self._lock:
            if self.connected:
                self._capabilities = self._probe_capabilities_while_idle_locked()
                if self._device is None:
                    raise CameraError("The connected Basler camera has no device information")
                effective = _effective_settings(self.settings, self._capabilities)
                return CameraState(True, self._device, self._capabilities, effective)

            factory = pylon.TlFactory.GetInstance()
            devices = list(factory.EnumerateDevices())
            if not devices:
                raise CameraError("No Basler camera was detected by pylon")
            selected_index, selected = _select_device(devices, self.settings)
            camera = pylon.InstantCamera(factory.CreateDevice(selected))
            try:
                camera.Open()
                device = _device_info(selected, selected_index)
                capabilities = _read_capabilities(camera, pylon, device)
                effective = _effective_settings(self.settings, capabilities)
                return CameraState(False, device, capabilities, effective)
            finally:
                if camera.IsOpen():
                    camera.Close()

    def apply_configuration(self, settings: CameraConfig) -> CameraState:
        with self._lock:
            normalized = _copy_settings(settings).normalized()
            selection_changed = (
                normalized.selection_mode != self.settings.selection_mode
                or normalized.device_id != self.settings.device_id
            )
            if selection_changed and self.connected:
                raise CameraError("Changing the selected camera requires reconnecting the camera service")

            if self._camera is None or self._pylon is None or not self._camera.IsOpen():
                previous_settings = self.settings
                self.settings = normalized
                try:
                    self.connect()
                    return self.state()
                except Exception:
                    self.settings = previous_settings
                    raise

            previous_settings = self.settings
            was_grabbing = bool(self._camera.IsGrabbing())
            if was_grabbing:
                self._camera.StopGrabbing()
            try:
                self._apply_settings_locked(normalized)
                self._capabilities = self._read_capabilities_locked()
                self.settings = normalized
            except Exception as exc:
                # Applying GenICam nodes is not an atomic camera operation. Make a
                # best-effort attempt to put the prior profile back before returning
                # a failure to the HMI. The requested profile is never persisted
                # unless the controller receives a successful CameraState.
                try:
                    self._apply_settings_locked(previous_settings)
                    self._capabilities = self._read_capabilities_locked()
                    self.settings = previous_settings
                except Exception:  # noqa: S110 - rollback attempt; the original failure is raised immediately below
                    pass
                if isinstance(exc, CameraError):
                    raise
                raise CameraError(f"Camera settings could not be applied: {exc}") from exc
            finally:
                if was_grabbing and self._camera.IsOpen():
                    self._camera.StartGrabbing(self._pylon.GrabStrategy_LatestImageOnly)

            if self._device is None or self._capabilities is None:
                raise CameraError("Camera state is unavailable after applying settings")
            return CameraState(
                True,
                self._device,
                self._capabilities,
                _effective_settings(self.settings, self._capabilities),
            )

    def _apply_settings_locked(self, settings: CameraConfig) -> None:
        if self._camera is None or self._pylon is None:
            raise CameraError("Basler camera is not open")
        camera = self._camera
        pylon = self._pylon

        # Image geometry must be changed while acquisition is idle. CameraDefault
        # deliberately preserves the current camera ROI. Maximum and Custom reset
        # offsets first because width/height maxima can depend on current offsets.
        offset_x_node = _first_node(camera, "OffsetX")
        offset_y_node = _first_node(camera, "OffsetY")
        width_node = _first_node(camera, "Width")
        height_node = _first_node(camera, "Height")

        if settings.resolution_mode != "CameraDefault":
            _try_set_minimum(offset_x_node, pylon)
            _try_set_minimum(offset_y_node, pylon)

            if settings.resolution_mode == "Maximum":
                if _node_writable(width_node, pylon) and not _try_set_maximum(width_node, pylon):
                    raise CameraError("Maximum camera width could not be applied")
                if _node_writable(height_node, pylon) and not _try_set_maximum(height_node, pylon):
                    raise CameraError("Maximum camera height could not be applied")
            else:
                if settings.width <= 0 or settings.height <= 0:
                    raise CameraError("Custom resolution requires positive width and height values")
                if not _set_numeric_node(width_node, settings.width, pylon, integer=True):
                    raise CameraError("Custom camera width could not be applied")
                if not _set_numeric_node(height_node, settings.height, pylon, integer=True):
                    raise CameraError("Custom camera height could not be applied")
                offset_x_cap = _numeric_capability(camera, pylon, "Offset X", ("OffsetX",), "px")
                offset_y_cap = _numeric_capability(camera, pylon, "Offset Y", ("OffsetY",), "px")
                if settings.center_roi:
                    requested_x = max(0.0, offset_x_cap.maximum / 2.0)
                    requested_y = max(0.0, offset_y_cap.maximum / 2.0)
                else:
                    requested_x = settings.offset_x
                    requested_y = settings.offset_y
                if _node_writable(offset_x_node, pylon):
                    if not _set_numeric_node(offset_x_node, requested_x, pylon, integer=True):
                        raise CameraError("Camera X offset could not be applied")
                elif requested_x > 0:
                    raise CameraError("The connected camera does not support the requested X offset")
                if _node_writable(offset_y_node, pylon):
                    if not _set_numeric_node(offset_y_node, requested_y, pylon, integer=True):
                        raise CameraError("Camera Y offset could not be applied")
                elif requested_y > 0:
                    raise CameraError("The connected camera does not support the requested Y offset")

        pixel_format = _first_node(camera, "PixelFormat")
        if settings.pixel_format:
            if not _set_enum_node(pixel_format, settings.pixel_format, pylon):
                available = ", ".join(_enum_values(pixel_format, pylon)) or "camera default only"
                raise CameraError(f"Pixel format {settings.pixel_format} is unavailable. Detected formats: {available}")

        exposure_auto_node = _first_node(camera, "ExposureAuto")
        exposure_auto = _normalized_auto_mode(settings.exposure_auto, allow_default=True)
        if exposure_auto != "CameraDefault":
            if not _set_enum_node(exposure_auto_node, exposure_auto, pylon):
                raise CameraError(f"Exposure auto mode {exposure_auto} is not supported")
            if exposure_auto == "Off" and settings.exposure_us > 0:
                exposure_node = _first_node(camera, "ExposureTime", "ExposureTimeAbs")
                if not _set_numeric_node(exposure_node, settings.exposure_us, pylon, integer=False):
                    raise CameraError("Manual exposure could not be written to the connected camera")

        gain_auto_node = _first_node(camera, "GainAuto")
        gain_auto = _normalized_auto_mode(settings.gain_auto, allow_default=True)
        if gain_auto != "CameraDefault":
            if not _set_enum_node(gain_auto_node, gain_auto, pylon):
                raise CameraError(f"Gain auto mode {gain_auto} is not supported")
            if gain_auto == "Off":
                gain_node = _first_node(camera, "Gain", "GainRaw")
                if not _set_numeric_node(gain_node, settings.gain_db, pylon, integer=False):
                    raise CameraError("Manual gain could not be written to the connected camera")

        # --- colour and tone ------------------------------------------------
        #
        # Every one of these is skipped unless the station asked for it, so a
        # camera keeps whatever it had and a station configured before these
        # existed is unaffected. A camera that cannot do one of them is only an
        # error when the station actually asked: a mono camera has no white
        # balance, and that is not a fault unless someone tried to set it.
        balance_white_auto = _normalized_auto_mode(
            settings.balance_white_auto, allow_default=True
        )
        if balance_white_auto != "CameraDefault":
            balance_white_node = _first_node(camera, "BalanceWhiteAuto")
            if not _set_enum_node(balance_white_node, balance_white_auto, pylon):
                raise CameraError(
                    f"White-balance auto mode {balance_white_auto} is not supported "
                    "by the connected camera"
                )

        requested_ratios = (
            ("Red", settings.balance_ratio_red),
            ("Green", settings.balance_ratio_green),
            ("Blue", settings.balance_ratio_blue),
        )
        if any(value > 0 for _, value in requested_ratios):
            if balance_white_auto == "Continuous":
                raise CameraError(
                    "Fixed white-balance ratios cannot be applied while the "
                    "white-balance auto mode is Continuous. Set it to Off."
                )
            selector = _first_node(camera, "BalanceRatioSelector")
            ratio_node = _first_node(camera, "BalanceRatio", "BalanceRatioAbs")
            for channel, value in requested_ratios:
                if value <= 0:
                    continue
                if not _set_enum_node(selector, channel, pylon):
                    raise CameraError(
                        f"The connected camera does not expose a {channel} "
                        "white-balance channel"
                    )
                if not _set_numeric_node(ratio_node, value, pylon, integer=False):
                    raise CameraError(
                        f"The {channel} white-balance ratio could not be written "
                        "to the connected camera"
                    )

        if settings.black_level_enabled:
            black_level_node = _first_node(camera, "BlackLevel", "BlackLevelRaw")
            if not _set_numeric_node(
                black_level_node, settings.black_level, pylon, integer=False
            ):
                raise CameraError("Black level could not be written to the connected camera")

        if settings.gamma_enabled:
            gamma_enable = _first_node(camera, "GammaEnable")
            if _node_writable(gamma_enable, pylon):
                _set_bool_node(gamma_enable, True, pylon)
            gamma_node = _first_node(camera, "Gamma")
            if not _set_numeric_node(gamma_node, settings.gamma, pylon, integer=False):
                raise CameraError("Gamma could not be written to the connected camera")

        # A frame-rate cap on a triggered camera throttles how fast triggers are
        # accepted, which would silently add latency to a cycle -- or make
        # WaitForFrameTriggerReady time out -- for a setting that describes
        # free-run cadence and nothing else. Triggered acquisition always runs
        # with the cap off, whatever the profile carries.
        limit_frame_rate = settings.frame_rate_enabled and settings.trigger_mode != "On"
        frame_rate_enable = _first_node(camera, "AcquisitionFrameRateEnable")
        if _node_writable(frame_rate_enable, pylon):
            if not _set_bool_node(frame_rate_enable, limit_frame_rate, pylon):
                raise CameraError("Frame-rate enable could not be written to the connected camera")
        if limit_frame_rate:
            frame_rate = _first_node(camera, "AcquisitionFrameRate", "AcquisitionFrameRateAbs")
            if not _set_numeric_node(frame_rate, settings.frame_rate_fps, pylon, integer=False):
                raise CameraError("Requested frame rate could not be written to the connected camera")

        trigger_selector = _first_node(camera, "TriggerSelector")
        selector_is_frame_start = _set_enum_node(trigger_selector, "FrameStart", pylon)
        if settings.trigger_mode == "On" and not selector_is_frame_start:
            available = ", ".join(_enum_values(trigger_selector, pylon)) or "none"
            raise CameraError(
                "FrameStart is not available as a trigger selector. "
                f"Detected selectors: {available}"
            )
        trigger_mode = _first_node(camera, "TriggerMode")
        if not _set_enum_node(trigger_mode, settings.trigger_mode, pylon):
            if settings.trigger_mode == "On":
                raise CameraError("Frame-start triggering is not supported by the connected camera")
        if settings.trigger_mode == "On":
            trigger_source = _first_node(camera, "TriggerSource")
            if not _set_enum_node(trigger_source, settings.trigger_source, pylon):
                available = ", ".join(_enum_values(trigger_source, pylon)) or "none"
                raise CameraError(
                    f"Trigger source {settings.trigger_source} is unavailable. "
                    f"Detected sources: {available}"
                )

    def _read_capabilities_locked(self) -> CameraCapabilities:
        if self._camera is None or self._pylon is None:
            raise CameraError("Basler camera is not open")
        return _read_capabilities(self._camera, self._pylon, self._device)

    def _probe_capabilities_while_idle_locked(self) -> CameraCapabilities:
        """Read capabilities with acquisition stopped, then restore grab state.

        This avoids model-specific assumptions and fixes a common pypylon/GenICam
        behavior where ROI nodes appear read-only only because the stream is active.
        The method is called only from controller operations that already serialize
        camera access.
        """

        if self._camera is None or self._pylon is None:
            raise CameraError("Basler camera is not open")
        was_grabbing = bool(self._camera.IsGrabbing())
        if was_grabbing:
            self._camera.StopGrabbing()
        try:
            return self._read_capabilities_locked()
        finally:
            if was_grabbing and self._camera.IsOpen():
                self._camera.StartGrabbing(self._pylon.GrabStrategy_LatestImageOnly)

    @staticmethod
    def _load_pylon() -> Any:
        try:
            from pypylon import pylon  # type: ignore
        except ImportError as exc:
            raise CameraError(
                "pypylon is not installed. Install requirements.txt and the Basler pylon runtime for this computer."
            ) from exc
        return pylon

    @property
    def connected(self) -> bool:
        return bool(self._camera is not None and self._camera.IsOpen())

    @property
    def description(self) -> str:
        if self._device:
            return f"Basler {self._device.display_name}"
        return "Basler camera (automatic first available)"

    @property
    def capabilities(self) -> CameraCapabilities | None:
        return self._capabilities

    @property
    def backend_name(self) -> str:
        return "pypylon"


def _grab_result_identifier(result: Any) -> str:
    for name in ("GetID", "GetBlockID", "ID", "BlockID"):
        try:
            value = getattr(result, name)
            value = value() if callable(value) else value
            if value is not None:
                return str(value)
        except Exception:  # noqa: S112 - probing alternative GenICam names; absence is the expected miss
            continue
    return ""


def _grab_result_timestamp(result: Any) -> int | None:
    for name in ("GetTimeStamp", "GetTimestamp", "TimeStamp", "Timestamp"):
        try:
            value = getattr(result, name)
            value = value() if callable(value) else value
            if value is not None:
                return int(value)
        except Exception:  # noqa: S112 - probing alternative GenICam names; absence is the expected miss
            continue
    return None


def _copy_settings(settings: CameraConfig) -> CameraConfig:
    return CameraConfig(**asdict(settings))


def _effective_settings(settings: CameraConfig, capabilities: CameraCapabilities) -> CameraConfig:
    """Return the persisted profile after applying camera increments and limits.

    Capability readings describe what the camera is doing now, while ``settings``
    records the user's intent. ``CameraDefault`` and a blank pixel format must remain
    defaults instead of silently becoming manual values after a capability probe.
    """

    width, height = capabilities.active_resolution
    exposure_us = (
        capabilities.exposure_us.current
        if settings.exposure_auto == "Off" and capabilities.exposure_us.available
        else settings.exposure_us
    )
    gain_db = (
        capabilities.gain_db.current
        if settings.gain_auto == "Off" and capabilities.gain_db.available
        else settings.gain_db
    )
    frame_rate_fps = (
        capabilities.frame_rate_hz.current
        if settings.frame_rate_enabled and capabilities.frame_rate_hz.available
        else settings.frame_rate_fps
    )
    return replace(
        settings,
        # Device identity is display-only in automatic first-device mode.
        device_id=settings.device_id if settings.selection_mode == "specific" else "",
        width=width,
        height=height,
        offset_x=int(round(capabilities.offset_x.current)),
        offset_y=int(round(capabilities.offset_y.current)),
        exposure_us=exposure_us,
        gain_db=gain_db,
        frame_rate_fps=frame_rate_fps,
    )


def _normalized_auto_mode(value: str, *, allow_default: bool = False) -> str:
    normalized = str(value or "CameraDefault").strip().lower()
    mapping = {"off": "Off", "once": "Once", "continuous": "Continuous"}
    if allow_default and normalized in {"cameradefault", "camera default", "default", ""}:
        return "CameraDefault"
    return mapping.get(normalized, "Off")


def _select_device(devices: list[Any], settings: CameraConfig) -> tuple[int, Any]:
    del settings
    # Station policy: no model/serial lock. Device identity is display-only,
    # and every station opens the first device returned by pylon.
    return 0, devices[0]


def _device_info(device: Any, index: int) -> CameraDeviceInfo:
    device_class = _device_text(device, "GetDeviceClass", "DeviceClass")
    transport = _device_text(device, "GetTlType", "TlType")
    if not transport:
        lowered = device_class.lower()
        if "usb" in lowered:
            transport = "USB3"
        elif "gige" in lowered or "gev" in lowered:
            transport = "GigE"
        elif device_class:
            transport = device_class
    return CameraDeviceInfo(
        index=index,
        model_name=_device_text(device, "GetModelName", "ModelName") or "Unknown Basler camera",
        serial_number=_device_text(device, "GetSerialNumber", "SerialNumber"),
        friendly_name=_device_text(device, "GetFriendlyName", "FriendlyName"),
        user_defined_name=_device_text(device, "GetUserDefinedName", "UserDefinedName"),
        device_class=device_class,
        transport=transport,
    )


def _device_text(device: Any, method_name: str, attribute_name: str) -> str:
    try:
        method = getattr(device, method_name, None)
        if callable(method):
            return str(method())
        value = getattr(device, attribute_name, "")
        return str(value) if value is not None else ""
    except Exception:
        return ""


def _first_node(camera: Any, *names: str) -> Any | None:
    for name in names:
        try:
            node = getattr(camera, name)
        except Exception:
            node = None
        if node is not None:
            return node
    return None


def _node_available(node: Any | None, pylon: Any) -> bool:
    if node is None:
        return False
    for method_name in ("IsReadable", "IsValid"):
        method = getattr(node, method_name, None)
        if callable(method):
            try:
                return bool(method())
            except Exception:
                return False
    checker = getattr(pylon, "IsAvailable", None)
    if callable(checker):
        try:
            return bool(checker(node))
        except Exception:
            return False
    return True


def _node_writable(node: Any | None, pylon: Any) -> bool:
    if node is None:
        return False
    method = getattr(node, "IsWritable", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            return False
    if not _node_available(node, pylon):
        return False
    checker = getattr(pylon, "IsWritable", None)
    if callable(checker):
        try:
            return bool(checker(node))
        except Exception:
            return False
    return True


def _node_number(node: Any, method_name: str, attribute_name: str, default: float = 0.0) -> float:
    try:
        method = getattr(node, method_name, None)
        if callable(method):
            return float(method())
        return float(getattr(node, attribute_name))
    except Exception:
        return float(default)


def _node_value(node: Any, default: Any = "") -> Any:
    try:
        getter = getattr(node, "GetValue", None)
        if callable(getter):
            return getter()
        return getattr(node, "Value")
    except Exception:
        return default


def _node_unit(node: Any, fallback: str = "") -> str:
    """Return a GenICam unit without assuming all camera models use dB/raw."""

    try:
        getter = getattr(node, "GetUnit", None)
        if callable(getter):
            value = str(getter() or "").strip()
            if value:
                return value
        value = str(getattr(node, "Unit", "") or "").strip()
        return value or fallback
    except Exception:
        return fallback


def _numeric_capability(
    camera: Any,
    pylon: Any,
    label: str,
    names: Iterable[str],
    unit: str,
) -> NumericCapability:
    node = _first_node(camera, *tuple(names))
    if not _node_available(node, pylon):
        return NumericCapability(label, unit=unit)
    current = _node_number(node, "GetValue", "Value")
    minimum = _node_number(node, "GetMin", "Min", current)
    maximum = _node_number(node, "GetMax", "Max", current)
    increment = _node_number(node, "GetInc", "Inc", 0.0)
    return NumericCapability(
        label,
        available=True,
        writable=_node_writable(node, pylon),
        minimum=minimum,
        maximum=maximum,
        increment=increment,
        current=current,
        unit=_node_unit(node, unit),
    )


def _enum_values(node: Any | None, pylon: Any) -> tuple[str, ...]:
    if not _node_available(node, pylon):
        return ()
    for method_name in ("GetSettableValues", "GetAllValues", "GetSymbolics"):
        getter = getattr(node, method_name, None)
        if callable(getter):
            try:
                return tuple(str(item) for item in getter())
            except Exception:  # noqa: S112 - probing alternative enumeration getters across pylon versions
                continue
    try:
        return tuple(str(item) for item in getattr(node, "Symbolics"))
    except Exception:
        return ()


def _set_enum_node(node: Any | None, requested: str, pylon: Any) -> bool:
    if not requested or not _node_writable(node, pylon):
        return False
    values = _enum_values(node, pylon)
    if values:
        match = next((item for item in values if item.lower() == requested.lower()), None)
        if match is None:
            return False
        requested = match
    try:
        setter = getattr(node, "SetValue", None)
        if callable(setter):
            setter(requested)
        else:
            node.Value = requested
        return True
    except Exception:
        return False


def _set_bool_node(node: Any | None, requested: bool, pylon: Any) -> bool:
    if not _node_writable(node, pylon):
        return False
    try:
        setter = getattr(node, "SetValue", None)
        if callable(setter):
            setter(bool(requested))
        else:
            node.Value = bool(requested)
        return True
    except Exception:
        return False


def _set_numeric_node(node: Any | None, requested: float, pylon: Any, *, integer: bool) -> bool:
    if not _node_writable(node, pylon):
        return False
    current = _node_number(node, "GetValue", "Value")
    minimum = _node_number(node, "GetMin", "Min", current)
    maximum = _node_number(node, "GetMax", "Max", current)
    increment = _node_number(node, "GetInc", "Inc", 0.0)
    value = align_numeric(requested, minimum, maximum, increment, integer=integer)
    try:
        setter = getattr(node, "SetValue", None)
        if callable(setter):
            setter(value)
        else:
            node.Value = value
        return True
    except Exception:
        return False


def _try_set_minimum(node: Any | None, pylon: Any) -> bool:
    if not _node_writable(node, pylon):
        return False
    try:
        method = getattr(node, "TrySetToMinimum", None)
        if callable(method):
            return bool(method())
    except Exception:  # noqa: S110 - TrySetToMinimum is optional; the explicit minimum path follows
        pass
    minimum = _node_number(node, "GetMin", "Min")
    return _set_numeric_node(node, minimum, pylon, integer=True)


def _try_set_maximum(node: Any | None, pylon: Any) -> bool:
    if not _node_writable(node, pylon):
        return False
    try:
        method = getattr(node, "TrySetToMaximum", None)
        if callable(method):
            return bool(method())
    except Exception:  # noqa: S110 - TrySetToMaximum is optional; the explicit maximum path follows
        pass
    maximum = _node_number(node, "GetMax", "Max")
    return _set_numeric_node(node, maximum, pylon, integer=True)


def _feature_number(camera: Any, names: Iterable[str], default: float = 0.0) -> float:
    for name in names:
        node = _first_node(camera, name)
        if node is None:
            continue
        value = _node_number(node, "GetValue", "Value", default)
        if value > 0:
            return value
    return float(default)


def _read_capabilities(camera: Any, pylon: Any, device: CameraDeviceInfo | None) -> CameraCapabilities:
    width = _numeric_capability(camera, pylon, "Width", ("Width",), "px")
    height = _numeric_capability(camera, pylon, "Height", ("Height",), "px")
    width_max_node = _first_node(camera, "WidthMax")
    height_max_node = _first_node(camera, "HeightMax")
    if _node_available(width_max_node, pylon):
        sensor_width = _node_number(width_max_node, "GetValue", "Value", width.maximum)
        width = replace(width, maximum=max(width.maximum, sensor_width))
    if _node_available(height_max_node, pylon):
        sensor_height = _node_number(height_max_node, "GetValue", "Value", height.maximum)
        height = replace(height, maximum=max(height.maximum, sensor_height))
    offset_x = _numeric_capability(camera, pylon, "Offset X", ("OffsetX",), "px")
    offset_y = _numeric_capability(camera, pylon, "Offset Y", ("OffsetY",), "px")
    exposure = _numeric_capability(camera, pylon, "Exposure", ("ExposureTime", "ExposureTimeAbs"), "us")
    gain = _numeric_capability(camera, pylon, "Gain", ("Gain",), "dB")
    if not gain.available:
        # Older/other Basler families may expose GainRaw rather than a dB Gain
        # feature. Preserve the camera-reported unit and avoid presenting raw
        # register values as decibels.
        gain = _numeric_capability(camera, pylon, "Gain", ("GainRaw",), "raw")
    frame_rate = _numeric_capability(
        camera,
        pylon,
        "Frame rate",
        ("AcquisitionFrameRate", "AcquisitionFrameRateAbs"),
        "Hz",
    )

    # White balance is read through BalanceRatio, whose value depends on which
    # channel BalanceRatioSelector currently points at. The range and increment
    # are what the technician needs; the per-channel current values are read
    # back individually when the panel is populated.
    balance_ratio = _numeric_capability(
        camera, pylon, "White balance ratio", ("BalanceRatio", "BalanceRatioAbs"), ""
    )
    balance_white_auto = _first_node(camera, "BalanceWhiteAuto")
    balance_selector = _first_node(camera, "BalanceRatioSelector")
    black_level = _numeric_capability(
        camera, pylon, "Black level", ("BlackLevel", "BlackLevelRaw"), ""
    )
    gamma_capability = _numeric_capability(camera, pylon, "Gamma", ("Gamma",), "")

    pixel_format = _first_node(camera, "PixelFormat")
    exposure_auto = _first_node(camera, "ExposureAuto")
    gain_auto = _first_node(camera, "GainAuto")
    frame_rate_enable = _first_node(camera, "AcquisitionFrameRateEnable")
    trigger_mode = _first_node(camera, "TriggerMode")
    trigger_source = _first_node(camera, "TriggerSource")
    sensor_width = int(round(_feature_number(camera, ("SensorWidth", "WidthMax"), width.maximum)))
    sensor_height = int(round(_feature_number(camera, ("SensorHeight", "HeightMax"), height.maximum)))
    return CameraCapabilities(
        device=device,
        sensor_width_px=max(sensor_width, int(round(width.maximum))),
        sensor_height_px=max(sensor_height, int(round(height.maximum))),
        width=width,
        height=height,
        offset_x=offset_x,
        offset_y=offset_y,
        exposure_us=exposure,
        gain_db=gain,
        frame_rate_hz=frame_rate,
        pixel_formats=_enum_values(pixel_format, pylon),
        current_pixel_format=str(_node_value(pixel_format, "")),
        exposure_auto_modes=_enum_values(exposure_auto, pylon),
        current_exposure_auto=str(_node_value(exposure_auto, "Off")),
        balance_ratio=balance_ratio,
        balance_white_auto_modes=_enum_values(balance_white_auto, pylon),
        current_balance_white_auto=str(_node_value(balance_white_auto, "Off")),
        balance_ratio_selectors=_enum_values(balance_selector, pylon),
        black_level=black_level,
        gamma=gamma_capability,
        gain_auto_modes=_enum_values(gain_auto, pylon),
        current_gain_auto=str(_node_value(gain_auto, "Off")),
        frame_rate_enable_available=_node_writable(frame_rate_enable, pylon),
        frame_rate_enabled=bool(_node_value(frame_rate_enable, False)),
        trigger_modes=_enum_values(trigger_mode, pylon),
        current_trigger_mode=str(_node_value(trigger_mode, "Off")),
        trigger_sources=_enum_values(trigger_source, pylon),
        current_trigger_source=str(_node_value(trigger_source, "")),
    )
