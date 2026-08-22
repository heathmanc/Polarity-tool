from __future__ import annotations

import cv2
import numpy as np

from battery_inspector.models import NormalizedRect
from battery_inspector.roi_geometry import (
    circle_masked_square_crop,
    coerce_circle_rect,
    ml_input_crop,
)
from battery_inspector.services.vision import crop_marking_region


def test_circle_rect_is_square_in_source_pixels_on_wide_image() -> None:
    rect = coerce_circle_rect(NormalizedRect(0.20, 0.20, 0.30, 0.30), 600, 300)
    width_px = rect.width * 600
    height_px = rect.height * 300
    assert abs(width_px - height_px) < 1.0


def test_circle_masked_crop_is_square_and_removes_corner_context() -> None:
    image = np.zeros((300, 600, 3), dtype=np.uint8)
    image[:] = (0, 0, 255)  # dangerous red surrounding context
    yy, xx = np.ogrid[:300, :600]
    mask = (xx - 300) ** 2 + (yy - 150) ** 2 <= 70**2
    image[mask] = (180, 180, 180)
    image[145:155, 265:335] = (20, 20, 20)

    crop, rect, metadata = circle_masked_square_crop(
        image,
        NormalizedRect(230 / 600, 80 / 300, 140 / 600, 140 / 300),
    )

    assert crop.shape[0] == crop.shape[1]
    assert metadata["roi_shape"] == "circle"
    # Corners are neutralized from inside-circle color, not preserved red context.
    corner = crop[0, 0].astype(int)
    assert int(corner[2]) < 230
    assert abs(rect.width * 600 - rect.height * 300) < 1.0


def test_training_and_production_circle_crop_contract_are_pixel_identical() -> None:
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    image[:] = (25, 60, 190)
    cv2.circle(image, (330, 180), 82, (170, 175, 180), -1)
    cv2.line(image, (288, 180), (372, 180), (20, 20, 20), 10)
    cv2.line(image, (330, 138), (330, 222), (20, 20, 20), 10)
    rect = NormalizedRect(248 / 640, 98 / 360, 164 / 640, 164 / 360)

    training_crop, training_rect, _metadata, contract = ml_input_crop(
        image,
        rect,
        "circle",
    )
    production_crop, production_rect = crop_marking_region(image, rect, "circle")

    assert contract == "taught_circle_masked_square_v1"
    assert np.array_equal(training_crop, production_crop)
    assert training_rect.to_dict() == production_rect.to_dict()
