from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ActivityTracker:
    """Track overlapping foreground operations without leaving a stale BUSY state.

    A single boolean is not sufficient once camera, PLC, and inspection workers can
    overlap.  Each operation owns a named activity token; finishing one token cannot
    accidentally clear another operation's state.
    """

    priorities: dict[str, int] = field(
        default_factory=lambda: {
            "inspection": 100,
            "camera": 80,
            "plc": 70,
            "startup": 60,
        }
    )
    _activities: dict[str, str] = field(default_factory=dict)

    def begin(self, key: str, reason: str) -> None:
        normalized_key = str(key).strip().lower()
        if not normalized_key:
            raise ValueError("Activity key cannot be blank")
        normalized_reason = str(reason or "WORKING").strip().upper()
        self._activities[normalized_key] = normalized_reason

    def end(self, key: str) -> None:
        self._activities.pop(str(key).strip().lower(), None)

    def contains(self, key: str) -> bool:
        return str(key).strip().lower() in self._activities

    @property
    def busy(self) -> bool:
        return bool(self._activities)

    @property
    def reason(self) -> str:
        if not self._activities:
            return ""
        key = max(
            self._activities,
            key=lambda item: (self.priorities.get(item, 0), item),
        )
        return self._activities[key]

    def snapshot(self) -> dict[str, str]:
        return dict(self._activities)
