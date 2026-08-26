from __future__ import annotations

from abc import ABC, abstractmethod
from threading import Lock
from typing import Any

from battery_inspector.config import PlcTagMap


class PlcError(RuntimeError):
    pass


class TriggerEdgeLatch:
    """Convert a polled PLC trigger level into one rising-edge request.

    A physical Logix tag may remain high longer than one HMI poll.  Without a
    latch the station would start another cycle every time the previous cycle
    finished while that tag was still true.  The latch rearms only after an
    observed false level.
    """

    def __init__(self) -> None:
        self._previous = False

    def observe(self, level: bool) -> bool:
        current = bool(level)
        rising = current and not self._previous
        self._previous = current
        return rising

    def reset(self) -> None:
        self._previous = False

    @property
    def armed(self) -> bool:
        return not self._previous


class PlcService(ABC):
    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_cycle_state(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def publish_result(self, *, passed: bool, busy: bool = False) -> None:
        raise NotImplementedError

    @abstractmethod
    def clear_result(self) -> None:
        """Publish the idle state with Busy/Complete/Pass/Fail all false."""

        raise NotImplementedError

    @abstractmethod
    def write_heartbeat(self, value: bool) -> bool:
        """Publish the HMI heartbeat output and return the written state."""

        raise NotImplementedError

    @abstractmethod
    def write_ready(self, ready: bool) -> bool:
        """Publish station readiness and return the written state."""

        raise NotImplementedError

    @abstractmethod
    def set_bypass(self, enabled: bool) -> bool:
        """Set the PLC bypass tag and return the verified/read-back state."""

        raise NotImplementedError

    @property
    @abstractmethod
    def connected(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        raise NotImplementedError


class MockPlcService(PlcService):
    def __init__(
        self,
        recipe_name: str = "GROUP31_XHD",
        recipe_number: int = 0,
        recipe_selector: str = "name",
    ) -> None:
        self.recipe_name = recipe_name
        self.recipe_number = max(0, int(recipe_number))
        self.recipe_selector = "number" if recipe_selector == "number" else "name"
        self._connected = False
        self._trigger = False
        self._heartbeat = False
        self._bypass = False
        self._acknowledge = False
        self._ready = False
        self._lock = Lock()
        self.last_result: dict[str, Any] = {}

    def connect(self) -> None:
        with self._lock:
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False

    def pulse_trigger(self) -> None:
        with self._lock:
            if not self._connected:
                raise PlcError("Mock PLC is not connected")
            self._trigger = True

    def set_acknowledge(self, value: bool) -> None:
        """Stand in for a controller raising or dropping the acknowledge bit."""

        with self._lock:
            if not self._connected:
                raise PlcError("Mock PLC is not connected")
            self._acknowledge = bool(value)

    def read_cycle_state(self) -> dict[str, Any]:
        with self._lock:
            if not self._connected:
                raise PlcError("Mock PLC is not connected")
            state = {
                "trigger": self._trigger,
                "recipe_name": self.recipe_name if self.recipe_selector == "name" else "",
                "recipe_number": self.recipe_number if self.recipe_selector == "number" else None,
                "recipe_selector": self.recipe_selector,
                "heartbeat": self._heartbeat,
                "bypass": self._bypass,
                "acknowledge": self._acknowledge,
            }
            self._trigger = False
            return state

    def publish_result(self, *, passed: bool, busy: bool = False) -> None:
        with self._lock:
            if not self._connected:
                raise PlcError("Mock PLC is not connected")
            complete = not busy
            self.last_result = {
                "passed": bool(passed) if complete else False,
                "fail": (not bool(passed)) if complete else False,
                "busy": bool(busy),
                "complete": complete,
            }

    def clear_result(self) -> None:
        with self._lock:
            if not self._connected:
                raise PlcError("Mock PLC is not connected")
            self.last_result = {}

    def write_heartbeat(self, value: bool) -> bool:
        with self._lock:
            if not self._connected:
                raise PlcError("Mock PLC is not connected")
            self._heartbeat = bool(value)
            return self._heartbeat

    def write_ready(self, ready: bool) -> bool:
        with self._lock:
            if not self._connected:
                raise PlcError("Mock PLC is not connected")
            self._ready = bool(ready)
            return self._ready

    def set_bypass(self, enabled: bool) -> bool:
        with self._lock:
            if not self._connected:
                raise PlcError("Mock PLC is not connected")
            self._bypass = bool(enabled)
            return self._bypass

    def snapshot(self) -> dict[str, Any]:
        """Return a technician-facing view of the simulated PLC handshake."""

        with self._lock:
            result = {
                "passed": None,
                "fail": False,
                "busy": False,
                "complete": False,
            }
            result.update(self.last_result)
            return {
                "connected": self._connected,
                "trigger": self._trigger,
                "recipe_name": self.recipe_name if self.recipe_selector == "name" else "",
                "recipe_number": self.recipe_number if self.recipe_selector == "number" else None,
                "recipe_selector": self.recipe_selector,
                "heartbeat": self._heartbeat,
                "bypass": self._bypass,
                "acknowledge": self._acknowledge,
                "ready": self._ready,
                **result,
            }

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def description(self) -> str:
        return "Mock Allen-Bradley PLC"


class AllenBradleyPlcService(PlcService):
    """pycomm3 LogixDriver adapter for CompactLogix/ControlLogix."""

    def __init__(
        self,
        address: str,
        tags: PlcTagMap,
        recipe_selector: str = "name",
    ) -> None:
        self.address = address
        self.tags = tags
        self.recipe_selector = "number" if recipe_selector == "number" else "name"
        self._driver: Any | None = None
        self._lock = Lock()
        self._heartbeat = False

    def connect(self) -> None:
        try:
            from pycomm3 import LogixDriver  # type: ignore
        except ImportError as exc:
            raise PlcError("pycomm3 is not installed. Install requirements.txt.") from exc

        with self._lock:
            driver = LogixDriver(self.address)
            if not driver.open():
                raise PlcError(f"Could not connect to PLC at {self.address}")
            self._driver = driver

    def disconnect(self) -> None:
        with self._lock:
            if self._driver is not None:
                self._driver.close()
            self._driver = None

    def _require_driver(self) -> Any:
        if self._driver is None:
            raise PlcError("PLC is not connected")
        return self._driver

    @staticmethod
    def _tag_value(result: Any) -> Any:
        if result is None:
            raise PlcError("PLC returned no value")
        if getattr(result, "error", None):
            raise PlcError(str(result.error))
        return getattr(result, "value", result)

    def read_cycle_state(self) -> dict[str, Any]:
        with self._lock:
            driver = self._require_driver()
            # The acknowledge tag is optional. Reading it in the same request
            # as the rest keeps the poll to one round trip whether or not the
            # handshake is configured.
            acknowledge_tag = str(self.tags.acknowledge or "").strip()
            names = [self.tags.trigger, self.tags.recipe_name, self.tags.bypass]
            if acknowledge_tag:
                names.append(acknowledge_tag)
            values = driver.read(*names)
            if not isinstance(values, list):
                values = [values]
            if len(values) != len(names):
                raise PlcError("Unexpected PLC read response")
            trigger = bool(self._tag_value(values[0]))
            recipe_value = self._tag_value(values[1])
            bypass = bool(self._tag_value(values[2]))
            acknowledge = bool(self._tag_value(values[3])) if acknowledge_tag else None
            if self.recipe_selector == "number":
                try:
                    recipe_number = int(recipe_value)
                except (TypeError, ValueError) as exc:
                    raise PlcError(
                        f"Recipe selector tag {self.tags.recipe_name} did not return an integer"
                    ) from exc
                recipe_name = ""
            else:
                recipe_name = str(recipe_value or "").strip()
                recipe_number = None
            return {
                "trigger": trigger,
                "recipe_name": recipe_name,
                "recipe_number": recipe_number,
                "recipe_selector": self.recipe_selector,
                "heartbeat": self._heartbeat,
                "bypass": bypass,
                "acknowledge": acknowledge,
            }

    def publish_result(self, *, passed: bool, busy: bool = False) -> None:
        with self._lock:
            driver = self._require_driver()
            complete = not busy
            writes = [
                (self.tags.busy, bool(busy)),
                (self.tags.complete, complete),
                (self.tags.pass_result, bool(passed) if complete else False),
                (self.tags.fail, (not bool(passed)) if complete else False),
            ]
            results = driver.write(*writes)
            self._raise_write_errors(results)

    def clear_result(self) -> None:
        with self._lock:
            driver = self._require_driver()
            results = driver.write(
                (self.tags.busy, False),
                (self.tags.complete, False),
                (self.tags.pass_result, False),
                (self.tags.fail, False),
            )
            self._raise_write_errors(results)

    @staticmethod
    def _raise_write_errors(results: Any) -> None:
        if not isinstance(results, list):
            results = [results]
        errors = [str(item.error) for item in results if getattr(item, "error", None)]
        if errors:
            raise PlcError("; ".join(errors))

    def write_heartbeat(self, value: bool) -> bool:
        with self._lock:
            driver = self._require_driver()
            desired = bool(value)
            results = driver.write((self.tags.heartbeat, desired))
            self._raise_write_errors(results)
            self._heartbeat = desired
            return desired

    def write_ready(self, ready: bool) -> bool:
        """Blank tag means the station does not publish readiness at all."""

        tag = str(self.tags.ready or "").strip()
        if not tag:
            return bool(ready)
        with self._lock:
            driver = self._require_driver()
            desired = bool(ready)
            results = driver.write((tag, desired))
            self._raise_write_errors(results)
            return desired

    def set_bypass(self, enabled: bool) -> bool:
        with self._lock:
            driver = self._require_driver()
            desired = bool(enabled)
            results = driver.write((self.tags.bypass, desired))
            self._raise_write_errors(results)
            readback = driver.read(self.tags.bypass)
            actual = bool(self._tag_value(readback))
            if actual != desired:
                raise PlcError(
                    f"Bypass tag read-back mismatch: requested {desired}, PLC reported {actual}"
                )
            return actual

    @property
    def connected(self) -> bool:
        return self._driver is not None

    @property
    def description(self) -> str:
        return f"Allen-Bradley Logix PLC ({self.address})"
