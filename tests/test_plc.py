from battery_inspector.services.plc import MockPlcService


def test_mock_plc_supports_full_trigger_and_result_handshake() -> None:
    plc = MockPlcService(recipe_name="MODEL_A")
    plc.connect()

    idle = plc.read_cycle_state()
    assert idle["trigger"] is False
    assert idle["recipe_name"] == "MODEL_A"

    plc.pulse_trigger()
    triggered = plc.read_cycle_state()
    assert triggered["trigger"] is True
    assert plc.read_cycle_state()["trigger"] is False

    plc.publish_result(passed=False, busy=True)
    assert plc.last_result == {
        "passed": False,
        "fail": False,
        "busy": True,
        "complete": False,
    }

    plc.publish_result(passed=True, busy=False)
    assert plc.last_result["passed"] is True
    assert plc.last_result["fail"] is False
    assert plc.last_result["complete"] is True
    plc.clear_result()
    cleared = plc.snapshot()
    assert cleared["complete"] is False
    assert cleared["passed"] is None
    assert cleared["fail"] is False


def test_mock_plc_snapshot_exposes_commissioning_handshake() -> None:
    plc = MockPlcService(recipe_name="MODEL_B")
    plc.connect()
    plc.pulse_trigger()

    armed = plc.snapshot()
    assert armed["connected"] is True
    assert armed["trigger"] is True
    assert armed["recipe_name"] == "MODEL_B"

    assert plc.read_cycle_state()["trigger"] is True
    plc.publish_result(passed=False, busy=False)

    complete = plc.snapshot()
    assert complete["trigger"] is False
    assert complete["complete"] is True
    assert complete["passed"] is False
    assert complete["fail"] is True


def test_mock_plc_can_select_recipe_by_integer_number() -> None:
    plc = MockPlcService(
        recipe_name="MODEL_B",
        recipe_number=42,
        recipe_selector="number",
    )
    plc.connect()

    state = plc.read_cycle_state()

    assert state["recipe_selector"] == "number"
    assert state["recipe_number"] == 42
    assert state["recipe_name"] == ""


def test_trigger_edge_latch_starts_once_while_tag_remains_high() -> None:
    from battery_inspector.services.plc import TriggerEdgeLatch

    latch = TriggerEdgeLatch()

    assert latch.observe(False) is False
    assert latch.observe(True) is True
    assert latch.observe(True) is False
    assert latch.observe(True) is False
    assert latch.observe(False) is False
    assert latch.observe(True) is True


def test_trigger_edge_latch_reset_rearms_after_service_replacement() -> None:
    from battery_inspector.services.plc import TriggerEdgeLatch

    latch = TriggerEdgeLatch()
    assert latch.observe(True) is True
    assert latch.observe(True) is False

    latch.reset()

    assert latch.armed is True
    assert latch.observe(True) is True


def test_mock_plc_heartbeat_is_controller_driven_and_bypass_round_trips() -> None:
    plc = MockPlcService(recipe_name="MODEL_C")
    plc.connect()

    assert plc.read_cycle_state()["heartbeat"] is False
    assert plc.write_heartbeat(True) is True
    state = plc.read_cycle_state()
    assert state["heartbeat"] is True
    assert state["bypass"] is False

    assert plc.set_bypass(True) is True
    assert plc.read_cycle_state()["bypass"] is True
    snapshot = plc.snapshot()
    assert snapshot["bypass"] is True
    assert snapshot["heartbeat"] is True

    assert plc.set_bypass(False) is False
    assert plc.read_cycle_state()["bypass"] is False


def test_logix_service_reads_bypass_and_writes_heartbeat_with_readback() -> None:
    from battery_inspector.config import PlcTagMap
    from battery_inspector.services.plc import AllenBradleyPlcService

    class Result:
        def __init__(self, value=None, error=None):
            self.value = value
            self.error = error

    class Driver:
        def __init__(self) -> None:
            self.values = {
                "Vision.Trigger": False,
                "Vision.Recipe": "MODEL_D",
                "Vision.Bypass": False,
                "Vision.Heartbeat": False,
            }
            self.writes = []

        def read(self, *tags):
            results = [Result(self.values[tag]) for tag in tags]
            return results[0] if len(results) == 1 else results

        def write(self, *pairs):
            self.writes.extend(pairs)
            for tag, value in pairs:
                self.values[tag] = value
            results = [Result(value) for _tag, value in pairs]
            return results[0] if len(results) == 1 else results

    tags = PlcTagMap(
        trigger="Vision.Trigger",
        recipe_name="Vision.Recipe",
        bypass="Vision.Bypass",
        heartbeat="Vision.Heartbeat",
    )
    service = AllenBradleyPlcService("192.168.1.10/1", tags)
    driver = Driver()
    service._driver = driver  # type: ignore[attr-defined]

    state = service.read_cycle_state()
    assert state["recipe_name"] == "MODEL_D"
    assert state["bypass"] is False

    assert service.write_heartbeat(True) is True
    assert driver.values["Vision.Heartbeat"] is True

    assert service.set_bypass(True) is True
    assert driver.values["Vision.Bypass"] is True
    assert service.read_cycle_state()["bypass"] is True

    service.publish_result(passed=False, busy=False)
    assert driver.values[tags.busy] is False
    assert driver.values[tags.complete] is True
    assert driver.values[tags.pass_result] is False
    assert driver.values[tags.fail] is True

    service.publish_result(passed=False, busy=True)
    assert driver.values[tags.complete] is False
    assert driver.values[tags.pass_result] is False
    assert driver.values[tags.fail] is False

    service.clear_result()
    assert driver.values[tags.busy] is False
    assert driver.values[tags.complete] is False
    assert driver.values[tags.pass_result] is False
    assert driver.values[tags.fail] is False


def test_logix_service_reads_integer_recipe_selector() -> None:
    from battery_inspector.config import PlcTagMap
    from battery_inspector.services.plc import AllenBradleyPlcService

    class Result:
        error = None

        def __init__(self, value):
            self.value = value

    class Driver:
        def read(self, *_tags):
            return [Result(False), Result(314), Result(False)]

    service = AllenBradleyPlcService(
        "192.168.1.10/1",
        PlcTagMap(),
        recipe_selector="number",
    )
    service._driver = Driver()  # type: ignore[attr-defined]

    state = service.read_cycle_state()

    assert state["recipe_selector"] == "number"
    assert state["recipe_number"] == 314
    assert state["recipe_name"] == ""
