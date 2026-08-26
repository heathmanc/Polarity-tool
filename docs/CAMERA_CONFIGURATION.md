# Camera Discovery and Configuration

## Selection policy

The production-facing selection policy is deliberately simple:

1. Enumerate Basler devices through pylon.
2. Use device 1, the first enumerated camera.
3. Display the detected model, serial number, and transport for verification.
4. Never require a technician to type a serial number.
5. Never reject a camera because its model or serial differs from another station.

When multiple cameras are connected, the HMI identifies device 1 as **AUTO SELECTED** and lists the remaining devices as available. The serial remains diagnostic information, not a required configuration field.

`Scan Physical Cameras` always enumerates physical pylon hardware, even when the currently active source is the demo image. In camera `auto` mode, startup uses the first physical device when available. If no device/runtime is available, the HMI remains usable with a clearly identified demo fallback; **Apply & Test Camera** will promote the station to a newly detected physical device.

If the first physical camera is present but cannot accept a profile saved on another station, `auto` mode retries the same camera with camera defaults and marks the health state **CAM DEFAULTS**. This is a commissioning state, not a model lock. The technician must review the live capability ranges and complete **Apply & Test Camera** before treating the profile as verified. Hardware apply-and-test errors are never converted into a successful demo-camera test.

## Settings workflow

Open **Settings → Camera** and use the following sequence:

1. Select **Scan Physical Cameras**.
2. Confirm the detected model and display-only serial.
3. Review the detected physical sensor resolution, maximum acquisition ROI, and active acquisition ROI.
4. Preserve the camera current/default acquisition ROI, choose the maximum detected acquisition ROI, or configure a custom ROI.
5. Select exposure/gain modes and values within the detected camera limits.
6. Configure frame-rate control and confirm the displayed production PLC
   Trigger tag.
7. Select **Apply & Test Camera**.
8. Confirm the returned image dimensions, mean image level, and display-sized test-frame preview.

Settings are persisted only after the camera accepts them and a test capture
succeeds. The preview is downscaled only for display; inspection ROI extraction
continues to use the full returned frame.
Production triggering is application-level: a rising edge of the configured PLC
Trigger tag requests one fresh frame. Camera hardware-line and technician-facing
software production trigger choices are not exposed. Overview provides the only
manual inspection action.

## Exposed profile fields

```text
Device selection     first available (technician-facing default)
Grab timeout         milliseconds
Pixel format         camera default or a detected supported format
Resolution           camera current/default, maximum acquisition ROI, or custom width/height
ROI placement        centered or explicit X/Y offsets
Exposure             camera default, manual, once, or continuous
Gain                 camera default, manual, once, or continuous
White balance        camera default, off, once, or continuous
Balance ratios       per-channel red/green/blue; 0 leaves the channel untouched
Black level          off, or an explicit value
Gamma                off, or an explicit value
Frame rate           camera controlled or an enabled limit
Production trigger   configured PLC Trigger tag
```

Every colour and tone field leaves the camera alone unless deliberately
enabled, so a station configured before they existed is unaffected. A camera
that cannot perform one of them raises an error only when the station actually
asked for it: a mono camera has no white balance, and that is not a fault until
someone tries to set it.

### White balance is an inspection setting

The silver/brass terminal-finish check compares the chroma of a terminal crop
against the one stored in the recipe reference. The comparison is differential,
so a constant colour cast largely cancels -- but a balance that drifts, or one
the camera is choosing for itself frame by frame, moves the measurement that
decides the check.

Set white balance deliberately, fix it, and **recapture every recipe reference
afterwards**. A reference captured under one balance and parts inspected under
another is the failure mode this design has.

## Live preview

The CAMERA IMAGE tab streams frames while a control is moved, so the effect of
an exposure, gain, white balance, black level, or gamma change is visible as it
is made rather than after an apply-and-test cycle.

While the preview runs:

- the settings on screen are written to the camera and **never to the station
  configuration**, so nothing is persisted until SAVE & APPLY;
- the station counts as camera-occupied, and **no inspection can be graded** --
  a manual trigger is refused and a PLC trigger is dropped for the controller's
  timeout to catch;
- stopping the preview restores the saved profile, so leaving without saving
  leaves the camera as it was found. If that restore fails, the HMI says so
  explicitly, because the camera is then carrying settings that are not the
  station's.

The preview stops on application shutdown, and refuses to start while an
inspection, a reference capture, a validation run, or an ML capture owns the
camera.

The HMI reads each numeric node's minimum, maximum, increment, current value, writability, and reported unit. Geometry capabilities are probed while acquisition is idle because many GenICam cameras lock ROI nodes while streaming. Requested values are aligned to the connected camera's advertised increments before being written. Cameras that expose `GainRaw` instead of a dB `Gain` feature are displayed in raw/camera units rather than being mislabeled.

## Per-camera commissioning checks

Before production release, repeat these checks for every camera model intended for deployment:

- the first enumerated device is consistently the intended camera;
- physical sensor, maximum acquisition ROI, and active ROI values are correct for the installed camera configuration;
- exposure and gain changes visibly affect a captured test frame;
- the chosen pixel format converts correctly into the BGR image used by OpenCV;
- the PLC Trigger tag starts one fresh inspection cycle;
- the Overview manual action captures one fresh inspection frame;
- the camera remains stable after power cycles, USB reconnects, and application restarts;
- the inspection image remains readable across the approved battery families.

## Current validation boundary

The mock camera exercises the same configuration model and ROI calculations in automated tests. Physical pypylon behavior must still be tested with each intended camera and installed pylon runtime. The supplied `camera_probe.py --grab` result for the acA5472-17uc demonstrates successful enumeration, capability reporting, and full-resolution capture; the HMI path must still be verified on the target workstation after this update.

## Camera settings and station busy state

Camera configuration is independent of PLC communications. A PLC fault or PLC
settings operation does not by itself prevent **APPLY & TEST CAMERA**.

When the camera is genuinely occupied by application startup or an active inspection, the request is accepted once and shown as queued. The controller automatically applies and verifies the profile after that camera operation finishes. This avoids requiring the technician to repeatedly press Apply and prevents a transient inspection from being reported as a permanent `SYSTEM BUSY` condition.
