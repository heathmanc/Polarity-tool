from __future__ import annotations

import cv2
import numpy as np

from battery_inspector.models import TerminalFinish
from battery_inspector.services.vision import TerminalFinishValidator


def _terminal_top(color: tuple[int, int, int], *, brightness: float = 1.0) -> np.ndarray:
    image = np.full((240, 240, 3), color, dtype=np.uint8)
    image = np.clip(image.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
    cv2.circle(image, (120, 120), 92, tuple(int(v) for v in image[0, 0]), -1)
    cv2.line(image, (55, 120), (185, 120), (25, 25, 25), 12, cv2.LINE_AA)
    cv2.circle(image, (82, 76), 15, (250, 250, 250), -1)
    return image


def test_silver_reference_allows_brightness_change() -> None:
    validator = TerminalFinishValidator()
    reference = _terminal_top((175, 175, 175))
    current = _terminal_top((175, 175, 175), brightness=1.18)

    result = validator.validate(current, reference, TerminalFinish.SILVER)

    assert result.evaluated is True
    assert result.detected == TerminalFinish.SILVER
    assert result.status == "TERMINAL_FINISH_MATCH"
    assert "terminal_finish_compare" in result.diagnostic_images


def test_brass_on_silver_recipe_is_a_mismatch() -> None:
    validator = TerminalFinishValidator()
    reference = _terminal_top((175, 175, 175))
    current = _terminal_top((65, 145, 195))

    result = validator.validate(current, reference, TerminalFinish.SILVER)

    assert result.evaluated is True
    assert result.detected == TerminalFinish.BRASS
    assert result.status == "TERMINAL_FINISH_MISMATCH"


def test_silver_on_brass_recipe_is_a_mismatch() -> None:
    validator = TerminalFinishValidator()
    reference = _terminal_top((65, 145, 195))
    current = _terminal_top((175, 175, 175))

    result = validator.validate(current, reference, TerminalFinish.BRASS)

    assert result.evaluated is True
    assert result.detected == TerminalFinish.SILVER
    assert result.status == "TERMINAL_FINISH_MISMATCH"


def test_legacy_unspecified_finish_bypasses_gate() -> None:
    result = TerminalFinishValidator().validate(
        _terminal_top((175, 175, 175)),
        _terminal_top((65, 145, 195)),
        TerminalFinish.UNSPECIFIED,
    )

    assert result.evaluated is False
    assert result.detected == TerminalFinish.UNSPECIFIED
    assert result.status == "TERMINAL_FINISH_NOT_CONFIGURED"
