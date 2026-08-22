from battery_inspector.activity import ActivityTracker


def test_overlapping_activities_cannot_clear_each_other() -> None:
    tracker = ActivityTracker()
    tracker.begin("plc", "CONFIGURING PLC")
    tracker.begin("camera", "CONFIGURING CAMERA")

    assert tracker.busy is True
    assert tracker.reason == "CONFIGURING CAMERA"

    tracker.end("plc")
    assert tracker.busy is True
    assert tracker.reason == "CONFIGURING CAMERA"

    tracker.end("camera")
    assert tracker.busy is False
    assert tracker.reason == ""


def test_inspection_has_highest_display_priority() -> None:
    tracker = ActivityTracker()
    tracker.begin("camera", "CONFIGURING CAMERA")
    tracker.begin("inspection", "INSPECTING")

    assert tracker.reason == "INSPECTING"

    tracker.end("inspection")
    assert tracker.reason == "CONFIGURING CAMERA"
