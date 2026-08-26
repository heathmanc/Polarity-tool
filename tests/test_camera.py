from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from battery_inspector.config import AppConfig, CameraConfig
from battery_inspector.services.camera import CameraError, MockCameraService, align_numeric


def test_align_numeric_clamps_and_snaps() -> None:
    assert align_numeric(101, 64, 200, 4, integer=True) == 100
    assert align_numeric(10, 64, 200, 4, integer=True) == 64
    assert align_numeric(999, 64, 200, 4, integer=True) == 200
    assert align_numeric(1.26, 0.0, 2.0, 0.1) == 1.3


def test_mock_camera_reports_capabilities_and_applies_image_roi(tmp_path: Path) -> None:
    image = np.zeros((120, 200, 3), dtype=np.uint8)
    image_path = tmp_path / "camera.png"
    assert cv2.imwrite(str(image_path), image)

    settings = CameraConfig(
        resolution_mode="Custom",
        width=100,
        height=80,
        offset_x=20,
        offset_y=10,
        exposure_us=2400.0,
        gain_db=2.5,
    )
    camera = MockCameraService(image_path, settings)
    camera.connect()

    capabilities = camera.capabilities
    assert capabilities is not None
    assert capabilities.maximum_resolution == (200, 120)
    assert capabilities.maximum_acquisition_resolution == (200, 120)
    assert capabilities.active_resolution == (100, 80)
    assert capabilities.device is not None
    assert capabilities.device.serial_number == "SIM-0001"

    frame = camera.grab()
    assert frame.shape == (80, 100, 3)


def test_config_migrates_flat_camera_fields_and_discards_serial(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "simulation": False,
                "camera_serial": "DO-NOT-REQUIRE-THIS",
                "camera_timeout_ms": 6500,
                "operator_name": "Tech",
            }
        ),
        encoding="utf-8",
    )

    config = AppConfig.load(path)
    assert config.simulation is False
    assert config.camera_backend == "basler"
    assert config.plc_backend == "pycomm3"
    assert config.camera.selection_mode == "first_available"
    assert config.camera.device_id == ""
    assert config.camera.timeout_ms == 6500
    assert not hasattr(config, "camera_serial")


def test_legacy_simulation_migrates_to_auto_camera_and_simulated_plc(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"simulation": True}), encoding="utf-8")

    config = AppConfig.load(path)

    assert config.camera_backend == "auto"
    assert config.plc_backend == "simulation"
    assert config.simulation is False


def test_camera_profile_cannot_persist_a_serial_binding() -> None:
    profile = CameraConfig(selection_mode="specific", device_id="SERIAL-SHOULD-BE-DISCARDED").normalized()

    assert profile.selection_mode == "first_available"
    assert profile.device_id == ""
    assert profile.uses_first_available is True


def test_v020_config_migrates_to_physical_camera_auto_without_plc_requirement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "simulation": True,
                "camera": {
                    "selection_mode": "specific",
                    "device_id": "SERIAL-SHOULD-BE-DISCARDED",
                    "timeout_ms": 4200,
                    "exposure_auto": "Off",
                    "exposure_us": 9997.0,
                },
                "plc_address": "192.168.1.10/1",
            }
        ),
        encoding="utf-8",
    )

    config = AppConfig.load(path)

    assert config.camera_backend == "auto"
    assert config.plc_backend == "simulation"
    assert config.camera.selection_mode == "first_available"
    assert config.camera.device_id == ""
    assert config.camera.timeout_ms == 4200
    assert config.camera.exposure_us == 9997.0


def test_config_round_trip_uses_nested_camera_profile(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = AppConfig(camera=CameraConfig(exposure_us=8123.4, resolution_mode="Maximum"))
    config.save(path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["camera"]["exposure_us"] == 8123.4
    assert "camera_serial" not in raw
    assert "selection_mode" not in raw["camera"]
    assert "device_id" not in raw["camera"]
    loaded = AppConfig.load(path)
    assert loaded.camera.exposure_us == 8123.4


class _FakeDevice:
    def __init__(self, serial: str) -> None:
        self._serial = serial

    def GetSerialNumber(self) -> str:
        return self._serial


def test_first_available_selection_ignores_serial_and_uses_device_zero() -> None:
    from battery_inspector.services.camera import _select_device

    devices = [_FakeDevice("FIRST"), _FakeDevice("SECOND")]
    index, selected = _select_device(
        devices,
        CameraConfig(selection_mode="first_available", device_id="SECOND"),
    )

    assert index == 0
    assert selected is devices[0]


def test_camera_defaults_remain_defaults_after_capability_probe(tmp_path: Path) -> None:
    image = np.zeros((120, 200, 3), dtype=np.uint8)
    image_path = tmp_path / "camera-defaults.png"
    assert cv2.imwrite(str(image_path), image)

    camera = MockCameraService(
        image_path,
        CameraConfig(
            pixel_format="",
            exposure_auto="CameraDefault",
            exposure_us=0.0,
            gain_auto="CameraDefault",
            gain_db=0.0,
        ),
    )
    camera.connect()
    state = camera.state()

    assert state.capabilities.current_pixel_format == "BGR8"
    assert state.capabilities.current_exposure_auto == "Off"
    assert state.settings.pixel_format == ""
    assert state.settings.exposure_auto == "CameraDefault"
    assert state.settings.gain_auto == "CameraDefault"


def test_mock_camera_centers_custom_acquisition_roi(tmp_path: Path) -> None:
    image = np.zeros((120, 200, 3), dtype=np.uint8)
    image_path = tmp_path / "centered-roi.png"
    assert cv2.imwrite(str(image_path), image)

    camera = MockCameraService(
        image_path,
        CameraConfig(
            resolution_mode="Custom",
            width=100,
            height=80,
            center_roi=True,
        ),
    )
    camera.connect()
    capabilities = camera.state().capabilities

    assert capabilities.active_resolution == (100, 80)
    assert capabilities.offset_x.current == 50
    assert capabilities.offset_y.current == 20


class _FakeNode:
    def __init__(
        self,
        value,
        *,
        minimum=0,
        maximum=0,
        increment=1,
        symbolics=(),
        writable=True,
        available=True,
        unit="",
    ) -> None:
        self.Value = value
        self.Min = minimum
        self.Max = maximum
        self.Inc = increment
        self.Symbolics = tuple(symbolics)
        self.writable = writable
        self.available = available
        self.Unit = unit

    def GetValue(self):
        return self.Value

    def SetValue(self, value) -> None:
        if self.Symbolics and value not in self.Symbolics:
            raise ValueError(value)
        if not self.Symbolics:
            value = min(max(value, self.Min), self.Max)
        self.Value = value

    def GetMin(self):
        return self.Min

    def GetMax(self):
        return self.Max

    def GetInc(self):
        return self.Inc

    def GetSymbolics(self):
        return self.Symbolics

    def GetUnit(self):
        return self.Unit

    def TrySetToMinimum(self) -> bool:
        self.Value = self.Min
        return True

    def TrySetToMaximum(self) -> bool:
        self.Value = self.Max
        return True


class _FakePylon:
    GrabStrategy_LatestImageOnly = object()

    @staticmethod
    def IsAvailable(node) -> bool:
        return bool(getattr(node, "available", False))

    @staticmethod
    def IsWritable(node) -> bool:
        return bool(getattr(node, "available", False) and getattr(node, "writable", False))


class _FakeCameraNodes:
    def __init__(self) -> None:
        self.Width = _FakeNode(5472, minimum=64, maximum=5496, increment=8)
        self.Height = _FakeNode(3648, minimum=64, maximum=3672, increment=2)
        self.SensorWidth = _FakeNode(5496, minimum=5496, maximum=5496, writable=False)
        self.SensorHeight = _FakeNode(3672, minimum=3672, maximum=3672, writable=False)
        self.WidthMax = _FakeNode(5496, minimum=5496, maximum=5496, writable=False)
        self.HeightMax = _FakeNode(3672, minimum=3672, maximum=3672, writable=False)
        self.OffsetX = _FakeNode(0, minimum=0, maximum=24, increment=8)
        self.OffsetY = _FakeNode(0, minimum=0, maximum=24, increment=2)
        self.ExposureTime = _FakeNode(5000.0, minimum=20.0, maximum=1_000_000.0, increment=1.0)
        self.Gain = _FakeNode(0.0, minimum=0.0, maximum=24.0, increment=0.1)
        self.AcquisitionFrameRate = _FakeNode(17.0, minimum=0.1, maximum=60.0, increment=0.1)
        self.PixelFormat = _FakeNode("BayerRG8", symbolics=("BayerRG8", "RGB8", "Mono8"))
        self.ExposureAuto = _FakeNode("Off", symbolics=("Off", "Once", "Continuous"))
        self.GainAuto = _FakeNode("Off", symbolics=("Off", "Once", "Continuous"))
        self.AcquisitionFrameRateEnable = _FakeNode(False, minimum=0, maximum=1)
        self.TriggerSelector = _FakeNode("FrameStart", symbolics=("FrameStart",))
        self.TriggerMode = _FakeNode("Off", symbolics=("Off", "On"))
        self.TriggerSource = _FakeNode("Software", symbolics=("Software", "Line1"))


class _FakeStreamingCameraNodes(_FakeCameraNodes):
    def __init__(self) -> None:
        super().__init__()
        self._grabbing = True
        self._open = True
        self.Width.writable = False
        self.Height.writable = False

    def IsGrabbing(self) -> bool:
        return self._grabbing

    def StopGrabbing(self) -> None:
        self._grabbing = False
        self.Width.writable = True
        self.Height.writable = True

    def StartGrabbing(self, _strategy) -> None:
        self._grabbing = True
        self.Width.writable = False
        self.Height.writable = False

    def IsOpen(self) -> bool:
        return self._open


def test_genicam_capability_probe_reports_sensor_and_active_resolution() -> None:
    from battery_inspector.services.camera import _read_capabilities

    camera = _FakeCameraNodes()
    capabilities = _read_capabilities(camera, _FakePylon(), None)

    assert capabilities.maximum_resolution == (5496, 3672)
    assert capabilities.active_resolution == (5472, 3648)
    assert capabilities.width.increment == 8
    assert capabilities.height.increment == 2
    assert capabilities.pixel_formats == ("BayerRG8", "RGB8", "Mono8")
    assert capabilities.exposure_us.minimum == 20.0
    assert capabilities.exposure_us.maximum == 1_000_000.0
    assert capabilities.trigger_sources == ("Software", "Line1")


def test_gain_raw_camera_reports_raw_units_instead_of_db() -> None:
    from battery_inspector.services.camera import _read_capabilities

    camera = _FakeCameraNodes()
    del camera.Gain
    camera.GainRaw = _FakeNode(12, minimum=0, maximum=255, increment=1, unit="raw")

    capabilities = _read_capabilities(camera, _FakePylon(), None)

    assert capabilities.gain_db.available is True
    assert capabilities.gain_db.current == 12
    assert capabilities.gain_db.unit == "raw"


def test_capability_probe_stops_stream_to_report_roi_writability() -> None:
    from battery_inspector.services.camera import BaslerCameraService

    camera = _FakeStreamingCameraNodes()
    service = BaslerCameraService()
    service._camera = camera  # type: ignore[attr-defined]
    service._pylon = _FakePylon()  # type: ignore[attr-defined]

    capabilities = service._probe_capabilities_while_idle_locked()  # type: ignore[attr-defined]

    assert capabilities.width.writable is True
    assert capabilities.height.writable is True
    assert camera.IsGrabbing() is True


def test_basler_profile_writes_custom_roi_exposure_gain_and_trigger() -> None:
    from battery_inspector.services.camera import BaslerCameraService

    camera = _FakeCameraNodes()
    service = BaslerCameraService()
    service._camera = camera  # type: ignore[attr-defined]
    service._pylon = _FakePylon()  # type: ignore[attr-defined]

    service._apply_settings_locked(  # type: ignore[attr-defined]
        CameraConfig(
            resolution_mode="Custom",
            width=3000,
            height=2000,
            center_roi=True,
            exposure_auto="Off",
            exposure_us=8123.4,
            gain_auto="Off",
            gain_db=2.53,
            frame_rate_enabled=True,
            frame_rate_fps=12.34,
            trigger_mode="On",
            trigger_source="Line1",
        )
    )

    assert camera.Width.Value == 3000
    assert camera.Height.Value == 2000
    assert camera.OffsetX.Value == 16  # half of the 24 px range, aligned to the 8 px increment
    assert camera.OffsetY.Value == 12
    assert camera.ExposureTime.Value == 8123.0
    assert camera.Gain.Value == 2.5
    # A frame-rate cap throttles how fast a triggered camera accepts triggers,
    # so triggered acquisition always runs with the cap off, whatever the
    # profile carries. See _apply_settings.
    assert camera.AcquisitionFrameRateEnable.Value is False
    assert camera.TriggerMode.Value == "On"
    assert camera.TriggerSource.Value == "Line1"


def test_a_free_running_profile_still_writes_the_frame_rate_cap() -> None:
    from battery_inspector.services.camera import BaslerCameraService

    camera = _FakeCameraNodes()
    service = BaslerCameraService()
    service._camera = camera  # type: ignore[attr-defined]
    service._pylon = _FakePylon()  # type: ignore[attr-defined]

    service._apply_settings_locked(  # type: ignore[attr-defined]
        CameraConfig(
            frame_rate_enabled=True,
            frame_rate_fps=12.34,
            trigger_mode="Off",
        )
    )

    assert camera.AcquisitionFrameRateEnable.Value is True
    assert camera.AcquisitionFrameRate.Value == 12.3


def test_camera_default_resolution_preserves_existing_acquisition_roi() -> None:
    from battery_inspector.services.camera import BaslerCameraService

    camera = _FakeCameraNodes()
    camera.Width.Value = 4200
    camera.Height.Value = 2800
    camera.OffsetX.Value = 16
    camera.OffsetY.Value = 10
    service = BaslerCameraService()
    service._camera = camera  # type: ignore[attr-defined]
    service._pylon = _FakePylon()  # type: ignore[attr-defined]

    service._apply_settings_locked(  # type: ignore[attr-defined]
        CameraConfig(resolution_mode="CameraDefault")
    )

    assert camera.Width.Value == 4200
    assert camera.Height.Value == 2800
    assert camera.OffsetX.Value == 16
    assert camera.OffsetY.Value == 10


def test_nested_legacy_max_resolution_flag_migrates_to_resolution_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "camera": {
                    "use_max_resolution": False,
                    "width": 3200,
                    "height": 2400,
                }
            }
        ),
        encoding="utf-8",
    )

    config = AppConfig.load(path)

    assert config.camera.resolution_mode == "Custom"
    assert config.camera.width == 3200
    assert config.camera.height == 2400


def test_mock_capture_returns_unique_fresh_frame_metadata(tmp_path: Path) -> None:
    image_path = tmp_path / "camera.png"
    assert cv2.imwrite(str(image_path), np.full((48, 64, 3), 25, dtype=np.uint8))
    camera = MockCameraService(image_path)
    camera.connect()

    first = camera.capture()
    second = camera.capture()

    assert first.fresh is True
    assert second.fresh is True
    assert first.sequence == 1
    assert second.sequence == 2
    assert first.frame_id != second.frame_id
    assert second.captured_monotonic_ns >= second.request_monotonic_ns
    assert first.backend_name == "MockCameraService"


def test_mock_capture_rereads_source_file_on_every_trigger(tmp_path: Path) -> None:
    image_path = tmp_path / "camera.png"
    first_image = np.full((50, 70, 3), 15, dtype=np.uint8)
    second_image = np.full((50, 70, 3), 210, dtype=np.uint8)
    assert cv2.imwrite(str(image_path), first_image)

    camera = MockCameraService(image_path)
    camera.connect()
    first = camera.capture()

    assert cv2.imwrite(str(image_path), second_image)
    second = camera.capture()

    assert float(first.image.mean()) < 20.0
    assert float(second.image.mean()) > 200.0
    assert first.frame_id != second.frame_id


class _CaptureResult:
    def __init__(self, identifier: str, value: int) -> None:
        self.identifier = identifier
        self.value = value
        self.released = False
        self.ErrorCode = 0
        self.ErrorDescription = ""

    def GrabSucceeded(self) -> bool:
        return True

    def Release(self) -> None:
        self.released = True

    def IsValid(self) -> bool:
        return True

    def GetID(self) -> str:
        return self.identifier

    def GetTimeStamp(self) -> int:
        return int(self.identifier.split("-")[-1])


class _ConvertedCapture:
    def __init__(self, result: _CaptureResult) -> None:
        self.result = result

    def GetArray(self) -> np.ndarray:
        return np.full((8, 12, 3), self.result.value, dtype=np.uint8)


class _CaptureConverter:
    def Convert(self, result: _CaptureResult) -> _ConvertedCapture:
        return _ConvertedCapture(result)


class _CapturePylon:
    TimeoutHandling_Return = object()
    TimeoutHandling_ThrowException = object()


class _FreeRunCaptureCamera:
    def __init__(self, stale, blocking) -> None:
        self.stale = list(stale)
        self.blocking = list(blocking)

    def RetrieveResult(self, timeout, _handling):
        if timeout == 0:
            return self.stale.pop(0) if self.stale else None
        if not self.blocking:
            raise RuntimeError("no blocking result")
        return self.blocking.pop(0)


def test_basler_free_run_capture_discards_queue_and_boundary_frame() -> None:
    from battery_inspector.services.camera import BaslerCameraService

    stale = _CaptureResult("FRAME-1", 10)
    boundary = _CaptureResult("FRAME-2", 20)
    fresh = _CaptureResult("FRAME-3", 30)
    service = BaslerCameraService(CameraConfig(trigger_mode="Off", timeout_ms=100))
    service._camera = _FreeRunCaptureCamera([stale], [boundary, fresh])  # type: ignore[attr-defined]
    service._pylon = _CapturePylon()  # type: ignore[attr-defined]
    service._converter = _CaptureConverter()  # type: ignore[attr-defined]

    frame = service.capture()

    assert frame.fresh is True
    assert frame.camera_frame_id == "FRAME-3"
    assert frame.stale_frames_discarded == 2
    assert float(frame.image.mean()) == 30.0
    assert stale.released is True
    assert boundary.released is True
    assert fresh.released is True


def test_basler_rejects_repeated_device_frame_identifier() -> None:
    from battery_inspector.services.camera import BaslerCameraService

    service = BaslerCameraService(CameraConfig(trigger_mode="Off", timeout_ms=100))
    service._camera = _FreeRunCaptureCamera(  # type: ignore[attr-defined]
        [],
        [_CaptureResult("FRAME-8", 20), _CaptureResult("FRAME-9", 30)],
    )
    service._pylon = _CapturePylon()  # type: ignore[attr-defined]
    service._converter = _CaptureConverter()  # type: ignore[attr-defined]
    service._last_camera_frame_id = "FRAME-9"  # type: ignore[attr-defined]

    with pytest.raises(CameraError, match="same device frame identifier"):
        service.capture()
