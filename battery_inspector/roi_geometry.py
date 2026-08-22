from __future__ import annotations

import numpy as np

from battery_inspector.models import NormalizedRect


CIRCLE_ROI_SHAPE = "circle"
RECT_ROI_SHAPE = "rectangle"
TAUGHT_CIRCLE_CROP_CONTRACT = "taught_circle_masked_square_v1"
LEGACY_RECT_CROP_CONTRACT = "legacy_rect_v1"


def normalize_roi_shape(value: str | None) -> str:
    shape = str(value or RECT_ROI_SHAPE).strip().lower()
    return CIRCLE_ROI_SHAPE if shape == CIRCLE_ROI_SHAPE else RECT_ROI_SHAPE


def circle_rect_from_center_radius(
    center_x: float,
    center_y: float,
    radius_px: float,
    image_width_px: int,
    image_height_px: int,
) -> NormalizedRect:
    """Return a normalized bounding rectangle for a *pixel-space* circle.

    Normalized x/y coordinates have different scales on a non-square image. A
    circle therefore normally has different normalized width and height values.
    """

    width = max(1, int(image_width_px))
    height = max(1, int(image_height_px))
    cx_px = float(center_x) * width
    cy_px = float(center_y) * height
    max_radius = max(
        1.0,
        min(cx_px, width - cx_px, cy_px, height - cy_px),
    )
    radius = min(max(1.0, float(radius_px)), max_radius)
    return NormalizedRect(
        x=(cx_px - radius) / width,
        y=(cy_px - radius) / height,
        width=(2.0 * radius) / width,
        height=(2.0 * radius) / height,
    ).clamped()


def coerce_circle_rect(
    rect: NormalizedRect,
    image_width_px: int,
    image_height_px: int,
    *,
    use_larger_extent: bool = False,
) -> NormalizedRect:
    """Coerce any normalized rectangle into a pixel-square circle bounding box.

    The center is preserved. By default the circle is inscribed in the supplied
    rectangle so an existing rectangle can be migrated without introducing new
    image context. ``use_larger_extent`` is useful while interactively drawing.
    """

    width = max(1, int(image_width_px))
    height = max(1, int(image_height_px))
    source = rect.clamped()
    cx = source.x + source.width / 2.0
    cy = source.y + source.height / 2.0
    width_px = source.width * width
    height_px = source.height * height
    diameter = max(width_px, height_px) if use_larger_extent else min(width_px, height_px)
    radius = max(1.0, diameter / 2.0)
    return circle_rect_from_center_radius(cx, cy, radius, width, height)


def circle_rect_from_drag(
    center_x: float,
    center_y: float,
    edge_x: float,
    edge_y: float,
    image_width_px: int,
    image_height_px: int,
) -> NormalizedRect:
    width = max(1, int(image_width_px))
    height = max(1, int(image_height_px))
    dx = (float(edge_x) - float(center_x)) * width
    dy = (float(edge_y) - float(center_y)) * height
    radius = float(np.hypot(dx, dy))
    return circle_rect_from_center_radius(
        float(center_x),
        float(center_y),
        radius,
        width,
        height,
    )


def circle_masked_square_crop(
    image: np.ndarray,
    rect: NormalizedRect,
) -> tuple[np.ndarray, NormalizedRect, dict[str, float | str]]:
    """Crop a taught circle as a square and neutralize everything outside it.

    The circle is the operator-facing ROI. The returned image is the exact
    square tensor source used for ML training/inference. Corners are filled with
    the median color from inside the circle so the model cannot use the red ring,
    case polarity symbol, washer, or other surrounding context as a shortcut.
    """

    if image is None or image.size == 0:
        raise ValueError("Cannot crop an empty image")
    height, width = image.shape[:2]
    circle_rect = coerce_circle_rect(rect, width, height)
    cx = (circle_rect.x + circle_rect.width / 2.0) * width
    cy = (circle_rect.y + circle_rect.height / 2.0) * height
    diameter = min(circle_rect.width * width, circle_rect.height * height)
    radius = max(1.0, diameter / 2.0)

    x1 = int(round(cx - radius))
    y1 = int(round(cy - radius))
    x2 = int(round(cx + radius))
    y2 = int(round(cy + radius))
    side = max(2, min(x2 - x1, y2 - y1))
    x2 = x1 + side
    y2 = y1 + side

    x1 = max(0, min(width - side, x1))
    y1 = max(0, min(height - side, y1))
    x2 = min(width, x1 + side)
    y2 = min(height, y1 + side)
    crop = image[y1:y2, x1:x2].copy()
    if crop.size == 0:
        raise ValueError("Circle ROI produced an empty crop")

    crop_h, crop_w = crop.shape[:2]
    center = (crop_w / 2.0, crop_h / 2.0)
    mask_radius = max(1.0, min(crop_w, crop_h) / 2.0 - 1.0)
    yy, xx = np.ogrid[:crop_h, :crop_w]
    mask = ((xx - center[0]) ** 2 + (yy - center[1]) ** 2) <= mask_radius**2

    if crop.ndim == 2:
        inside = crop[mask]
        neutral = float(np.median(inside)) if inside.size else 127.0
        crop[~mask] = np.asarray(neutral, dtype=crop.dtype)
    else:
        inside = crop[mask]
        if inside.size:
            neutral = np.median(inside.reshape(-1, crop.shape[2]), axis=0)
        else:
            neutral = np.full((crop.shape[2],), 127.0)
        crop[~mask] = np.asarray(neutral, dtype=crop.dtype)

    metadata: dict[str, float | str] = {
        "roi_shape": CIRCLE_ROI_SHAPE,
        "circle_center_x_px": float(crop_w / 2.0),
        "circle_center_y_px": float(crop_h / 2.0),
        "circle_radius_px": float(mask_radius),
        "square_width_px": float(crop_w),
        "square_height_px": float(crop_h),
        "outside_circle_fill": "inside_median",
    }
    return crop, circle_rect, metadata


def ml_input_crop(
    image: np.ndarray,
    rect: NormalizedRect,
    shape: str | None,
) -> tuple[np.ndarray, NormalizedRect, dict[str, float | str], str]:
    """Build the exact image source used by both ML training and inference.

    This function is intentionally shared by the guided training workflow and
    the production inspection pipeline. Keeping one implementation prevents a
    model from being trained on one crop convention and validated on another.

    Circle ROIs are converted to a pixel-square crop and pixels outside the
    operator-taught circle are neutralized. Legacy rectangle recipes retain the
    historical rectangular crop contract for backward compatibility.
    """

    if image is None or image.size == 0:
        raise ValueError("Cannot crop an empty image")
    normalized_shape = normalize_roi_shape(shape)
    if normalized_shape == CIRCLE_ROI_SHAPE:
        crop, effective_rect, metadata = circle_masked_square_crop(image, rect)
        return crop, effective_rect, metadata, TAUGHT_CIRCLE_CROP_CONTRACT

    height, width = image.shape[:2]
    source = rect.clamped()
    x1 = max(0, min(width - 1, int(round(source.x * width))))
    y1 = max(0, min(height - 1, int(round(source.y * height))))
    x2 = max(x1 + 1, min(width, int(round((source.x + source.width) * width))))
    y2 = max(y1 + 1, min(height, int(round((source.y + source.height) * height))))
    crop = image[y1:y2, x1:x2].copy()
    if crop.size == 0:
        raise ValueError("Rectangle ROI produced an empty crop")
    metadata: dict[str, float | str] = {
        "roi_shape": RECT_ROI_SHAPE,
        "square_width_px": float(crop.shape[1]),
        "square_height_px": float(crop.shape[0]),
        "outside_circle_fill": "not_applicable",
    }
    return crop, source, metadata, LEGACY_RECT_CROP_CONTRACT


def circle_points(rect: NormalizedRect, segments: int = 40) -> list[tuple[float, float]]:
    source = rect.clamped()
    cx = source.x + source.width / 2.0
    cy = source.y + source.height / 2.0
    rx = source.width / 2.0
    ry = source.height / 2.0
    count = max(12, int(segments))
    return [
        (
            cx + rx * float(np.cos(2.0 * np.pi * index / count)),
            cy + ry * float(np.sin(2.0 * np.pi * index / count)),
        )
        for index in range(count)
    ]
