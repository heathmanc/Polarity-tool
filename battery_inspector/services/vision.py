from __future__ import annotations

import hashlib
import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import monotonic_ns, perf_counter
from typing import Any, Callable
from uuid import uuid4

import cv2
import numpy as np

from battery_inspector.build_info import (
    INSPECTION_ENGINE,
    MANIFEST_SCHEMA_VERSION,
    software_build_info,
)
from battery_inspector.evidence import (
    FailureRetentionPolicy,
    FailureRetentionReport,
    apply_failure_retention,
    assess_image_quality,
    save_jpeg,
    save_png,
    write_json_atomic,
)
from battery_inspector.models import (
    InspectionCycleState,
    InspectionDisposition,
    InspectionResult,
    LocatorSettings,
    Marking,
    MarkingClassifierSettings,
    NormalizedRect,
    Recipe,
    TerminalFinish,
    TerminalInspection,
    TerminalRecipe,
)
from battery_inspector.services.camera import CameraFrame
from battery_inspector.services.markings import (
    GeometricStampClassifier,
    GeometricStampThresholds,
    TerminalTopNormalization,
)
from battery_inspector.services.ml import MlModelError, OnnxPolarityModel
from battery_inspector.roi_geometry import (
    CIRCLE_ROI_SHAPE,
    circle_points,
    ml_input_crop,
    normalize_roi_shape,
)


class VisionError(RuntimeError):
    pass


class BatteryLocationError(VisionError):
    pass


StageCallback = Callable[[InspectionCycleState, str], None]


def rect_within(parent: NormalizedRect, child: NormalizedRect) -> NormalizedRect:
    """Map a parent-relative rectangle into the parent's coordinate system."""

    return NormalizedRect(
        x=parent.x + child.x * parent.width,
        y=parent.y + child.y * parent.height,
        width=child.width * parent.width,
        height=child.height * parent.height,
    ).clamped()


def crop_normalized(image: np.ndarray, rect: NormalizedRect) -> np.ndarray:
    if image.size == 0:
        raise VisionError("Cannot crop an empty image")
    height, width = image.shape[:2]
    rect = rect.clamped()
    x1 = max(0, min(width - 1, round(rect.x * width)))
    y1 = max(0, min(height - 1, round(rect.y * height)))
    x2 = max(x1 + 1, min(width, round((rect.x + rect.width) * width)))
    y2 = max(y1 + 1, min(height, round((rect.y + rect.height) * height)))
    return image[y1:y2, x1:x2].copy()


def crop_marking_region(
    image: np.ndarray,
    rect: NormalizedRect,
    shape: str,
) -> tuple[np.ndarray, NormalizedRect]:
    """Extract a marking region using the recipe's operator-taught geometry."""

    crop, effective_rect, _metadata, _contract = ml_input_crop(image, rect, shape)
    return crop, effective_rect


def _rect_pixel_bounds(rect: NormalizedRect, width: int, height: int) -> tuple[int, int, int, int]:
    normalized = rect.clamped()
    x1 = max(0, min(width - 1, round(normalized.x * width)))
    y1 = max(0, min(height - 1, round(normalized.y * height)))
    x2 = max(x1 + 1, min(width, round((normalized.x + normalized.width) * width)))
    y2 = max(y1 + 1, min(height, round((normalized.y + normalized.height) * height)))
    return x1, y1, x2, y2


def _rect_corners_px(rect: NormalizedRect, width: int, height: int) -> np.ndarray:
    x1, y1, x2, y2 = _rect_pixel_bounds(rect, width, height)
    return np.float32([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])


def _polygon_normalized(points: np.ndarray, width: int, height: int) -> list[tuple[float, float]]:
    flattened = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    return [
        (
            float(min(1.0, max(0.0, x / max(width, 1)))),
            float(min(1.0, max(0.0, y / max(height, 1)))),
        )
        for x, y in flattened
    ]


def _bounding_rect_from_polygon(points: np.ndarray, width: int, height: int) -> NormalizedRect:
    flattened = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    x1 = max(0.0, float(np.min(flattened[:, 0])))
    y1 = max(0.0, float(np.min(flattened[:, 1])))
    x2 = min(float(width), float(np.max(flattened[:, 0])))
    y2 = min(float(height), float(np.max(flattened[:, 1])))
    return NormalizedRect(
        x=x1 / max(width, 1),
        y=y1 / max(height, 1),
        width=max(1.0, x2 - x1) / max(width, 1),
        height=max(1.0, y2 - y1) / max(height, 1),
    ).clamped()


def _safe_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(slots=True)
class BatteryLocation:
    """One current-frame registration against a recipe reference image."""

    aligned_battery: np.ndarray
    reference_battery: np.ndarray
    battery_roi: NormalizedRect
    battery_polygon: list[tuple[float, float]]
    reference_to_current: np.ndarray
    reference_image_size: tuple[int, int]
    current_image_size: tuple[int, int]
    reference_battery_bounds: tuple[int, int, int, int]
    metrics: dict[str, Any] = field(default_factory=dict)

    def map_battery_rect(self, rect: NormalizedRect) -> list[tuple[float, float]]:
        """Map a battery-relative rectangle into current-frame normalized points."""

        ref_width, ref_height = self.reference_image_size
        current_width, current_height = self.current_image_size
        bx1, by1, bx2, by2 = self.reference_battery_bounds
        battery_width = max(1, bx2 - bx1)
        battery_height = max(1, by2 - by1)
        local = rect.clamped()
        points = np.float32(
            [
                [bx1 + local.x * battery_width, by1 + local.y * battery_height],
                [
                    bx1 + (local.x + local.width) * battery_width,
                    by1 + local.y * battery_height,
                ],
                [
                    bx1 + (local.x + local.width) * battery_width,
                    by1 + (local.y + local.height) * battery_height,
                ],
                [
                    bx1 + local.x * battery_width,
                    by1 + (local.y + local.height) * battery_height,
                ],
            ]
        ).reshape(1, 4, 2)
        transformed = cv2.perspectiveTransform(points, self.reference_to_current)[0]
        return _polygon_normalized(transformed, current_width, current_height)

    def map_battery_points(
        self,
        points_normalized: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        """Map arbitrary battery-relative normalized points into current-frame points."""

        if not points_normalized:
            return []
        current_width, current_height = self.current_image_size
        bx1, by1, bx2, by2 = self.reference_battery_bounds
        battery_width = max(1, bx2 - bx1)
        battery_height = max(1, by2 - by1)
        points = np.float32(
            [
                [bx1 + x * battery_width, by1 + y * battery_height]
                for x, y in points_normalized
            ]
        ).reshape(1, -1, 2)
        transformed = cv2.perspectiveTransform(points, self.reference_to_current)[0]
        return _polygon_normalized(transformed, current_width, current_height)


class BatteryLocator(ABC):
    """Replaceable battery-pose locator contract."""

    ready: bool = False
    status: str = "NOT_CONFIGURED"

    def readiness_issues(self, recipe: Recipe) -> list[str]:
        del recipe
        return [] if self.ready else [f"BATTERY_LOCATOR_NOT_READY:{self.status}"]

    @abstractmethod
    def locate(self, image: np.ndarray, recipe: Recipe) -> BatteryLocation:
        raise NotImplementedError


class RecipeBatteryLocator(BatteryLocator):
    """Preview-only identity locator retained for tests and offline review."""

    ready = False
    status = "TAUGHT_REFERENCE_ROI_ONLY"

    def locate(self, image: np.ndarray, recipe: Recipe) -> BatteryLocation:
        reference = image
        if recipe.reference_image and Path(recipe.reference_image.path).is_file():
            loaded = cv2.imread(str(recipe.reference_image.path), cv2.IMREAD_COLOR)
            if loaded is not None:
                reference = loaded
        ref_height, ref_width = reference.shape[:2]
        cur_height, cur_width = image.shape[:2]
        ref_bounds = _rect_pixel_bounds(recipe.battery_roi, ref_width, ref_height)
        reference_battery = reference[ref_bounds[1] : ref_bounds[3], ref_bounds[0] : ref_bounds[2]].copy()
        aligned_battery = crop_normalized(image, recipe.battery_roi)
        corners = _rect_corners_px(recipe.battery_roi, cur_width, cur_height)
        return BatteryLocation(
            aligned_battery=aligned_battery,
            reference_battery=reference_battery,
            battery_roi=recipe.battery_roi,
            battery_polygon=_polygon_normalized(corners, cur_width, cur_height),
            reference_to_current=np.eye(3, dtype=np.float64),
            reference_image_size=(ref_width, ref_height),
            current_image_size=(cur_width, cur_height),
            reference_battery_bounds=ref_bounds,
            metrics={"method": self.status, "preview_only": True},
        )


@dataclass(slots=True)
class _ReferenceFeatureCache:
    key: str
    reference_image: np.ndarray
    reference_battery: np.ndarray
    reference_size: tuple[int, int]
    battery_bounds: tuple[int, int, int, int]
    detector_name: str
    scale: float
    keypoints: list[Any]
    descriptors: np.ndarray
    feature_count: int
    orientation_mask: np.ndarray
    masked_terminal_count: int


class ReferenceFeatureBatteryLocator(BatteryLocator):
    """Register the current battery to a taught reference with OpenCV features.

    The feature detector runs on a downscaled copy for predictable cycle time,
    while the estimated homography and all evidence crops use full-resolution
    coordinates. Terminal ROIs are extracted from a perspective-aligned battery
    image, so recipe geometry follows translation and rotation without YOLO.
    """

    ready = True
    status = "REFERENCE_FEATURE_HOMOGRAPHY"

    def __init__(self) -> None:
        self._cache: dict[str, _ReferenceFeatureCache] = {}
        self._lock = RLock()
        if not hasattr(cv2, "SIFT_create") and not hasattr(cv2, "ORB_create"):
            self.ready = False
            self.status = "NO_SUPPORTED_OPENCV_FEATURE_DETECTOR"

    @staticmethod
    def _scaled(image: np.ndarray, maximum_dimension: int) -> tuple[np.ndarray, float]:
        height, width = image.shape[:2]
        scale = min(1.0, float(maximum_dimension) / float(max(height, width)))
        if scale >= 0.999:
            return image, 1.0
        resized = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        return resized, scale

    @staticmethod
    def _expanded_rect(rect: NormalizedRect, margin: float) -> NormalizedRect:
        return NormalizedRect(
            x=rect.x - margin,
            y=rect.y - margin,
            width=rect.width + margin * 2.0,
            height=rect.height + margin * 2.0,
        ).clamped()

    @classmethod
    def _orientation_mask(
        cls,
        recipe: Recipe,
        battery_width: int,
        battery_height: int,
    ) -> np.ndarray:
        """Mask case features used to resolve the 180-degree direction.

        Complete terminal-search regions are excluded. Otherwise a red ring or
        polarity stamp could decide orientation and normalize an incorrect part
        into a false PASS.
        """

        mask = np.full((battery_height, battery_width), 255, dtype=np.uint8)
        border_x = max(2, int(round(battery_width * 0.025)))
        border_y = max(2, int(round(battery_height * 0.025)))
        mask[:border_y, :] = 0
        mask[-border_y:, :] = 0
        mask[:, :border_x] = 0
        mask[:, -border_x:] = 0
        for terminal in recipe.terminals:
            if not terminal.enabled:
                continue
            rect = cls._expanded_rect(terminal.search_roi, 0.035)
            x1, y1, x2, y2 = _rect_pixel_bounds(
                rect,
                battery_width,
                battery_height,
            )
            mask[y1:y2, x1:x2] = 0
        return mask

    @staticmethod
    def _orientation_representation(
        image: np.ndarray,
        mask: np.ndarray,
        maximum_dimension: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        scale = min(
            1.0,
            float(max(128, maximum_dimension)) / float(max(image.shape[:2])),
        )
        if scale < 0.999:
            image = cv2.resize(
                image,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA,
            )
            mask = cv2.resize(
                mask,
                (image.shape[1], image.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        magnitude = cv2.magnitude(grad_x, grad_y)
        magnitude = cv2.GaussianBlur(magnitude, (0, 0), 1.1)
        return magnitude, mask > 0

    @staticmethod
    def _masked_correlation(
        first: np.ndarray,
        second: np.ndarray,
        mask: np.ndarray,
    ) -> float:
        if first.shape != second.shape or first.shape != mask.shape:
            return -1.0
        left = first[mask].astype(np.float64)
        right = second[mask].astype(np.float64)
        if left.size < 256:
            return -1.0
        left -= float(left.mean())
        right -= float(right.mean())
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator <= 1e-12:
            return -1.0
        return float(np.clip(np.dot(left, right) / denominator, -1.0, 1.0))

    @staticmethod
    def _rotation_matrix_180(width: int, height: int) -> np.ndarray:
        return np.array(
            [
                [-1.0, 0.0, float(width - 1)],
                [0.0, -1.0, float(height - 1)],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )

    @staticmethod
    def _choose_detector(settings: LocatorSettings) -> tuple[Any, str, int]:
        requested = settings.detector.upper()
        if requested in {"AUTO", "SIFT"} and hasattr(cv2, "SIFT_create"):
            detector = cv2.SIFT_create(
                nfeatures=settings.feature_count,
                contrastThreshold=0.015,
                edgeThreshold=12,
                sigma=1.6,
            )
            return detector, "SIFT", cv2.NORM_L2
        if requested == "SIFT":
            raise BatteryLocationError("SIFT was requested but is not available in this OpenCV build")
        if hasattr(cv2, "ORB_create"):
            detector = cv2.ORB_create(
                nfeatures=settings.feature_count,
                scaleFactor=1.2,
                nlevels=8,
                edgeThreshold=21,
                fastThreshold=7,
            )
            return detector, "ORB", cv2.NORM_HAMMING
        raise BatteryLocationError("No supported OpenCV feature detector is available")

    @staticmethod
    def _cache_key(recipe: Recipe) -> str:
        reference = recipe.reference_image
        return _safe_json_hash(
            {
                "recipe_id": recipe.recipe_id,
                "revision": recipe.revision,
                "reference_sha256": reference.sha256 if reference else "",
                "battery_roi": recipe.battery_roi.to_dict(),
                "orientation_reference": recipe.orientation_reference,
                "terminal_masks": [
                    {
                        "key": terminal.key,
                        "enabled": terminal.enabled,
                        "search_roi": terminal.search_roi.to_dict(),
                    }
                    for terminal in recipe.terminals
                ],
                "settings": recipe.locator_settings.to_dict(),
            }
        )

    def _load_reference(self, recipe: Recipe) -> _ReferenceFeatureCache:
        if not recipe.reference_image or not Path(recipe.reference_image.path).is_file():
            raise BatteryLocationError("Recipe reference image is missing")
        key = self._cache_key(recipe)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        settings = recipe.locator_settings.normalized()
        reference = cv2.imread(str(recipe.reference_image.path), cv2.IMREAD_COLOR)
        if reference is None or reference.size == 0:
            raise BatteryLocationError("Recipe reference image could not be opened")
        ref_height, ref_width = reference.shape[:2]
        bounds = _rect_pixel_bounds(recipe.battery_roi, ref_width, ref_height)
        reference_battery = reference[bounds[1] : bounds[3], bounds[0] : bounds[2]].copy()
        scaled, scale = self._scaled(reference, settings.max_detection_dimension)
        gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
        mask = np.zeros_like(gray, dtype=np.uint8)
        x1, y1, x2, y2 = _rect_pixel_bounds(
            recipe.battery_roi,
            scaled.shape[1],
            scaled.shape[0],
        )
        margin_x = max(2, int((x2 - x1) * 0.015))
        margin_y = max(2, int((y2 - y1) * 0.015))
        cv2.rectangle(
            mask,
            (min(x2 - 1, x1 + margin_x), min(y2 - 1, y1 + margin_y)),
            (max(x1 + 1, x2 - margin_x), max(y1 + 1, y2 - margin_y)),
            255,
            -1,
        )
        # Pose features must not come from the polarity stamping or red ring.
        # Otherwise a bad part could influence its own 180-degree normalization.
        masked_terminal_count = 0
        for terminal in recipe.terminals:
            if not terminal.enabled:
                continue
            terminal_full = rect_within(
                recipe.battery_roi,
                self._expanded_rect(terminal.search_roi, 0.035),
            )
            tx1, ty1, tx2, ty2 = _rect_pixel_bounds(
                terminal_full,
                scaled.shape[1],
                scaled.shape[0],
            )
            mask[ty1:ty2, tx1:tx2] = 0
            masked_terminal_count += 1
        detector, detector_name, _norm = self._choose_detector(settings)
        keypoints, descriptors = detector.detectAndCompute(gray, mask)
        if descriptors is None or len(keypoints) < settings.minimum_matches:
            raise BatteryLocationError(
                "Reference battery has too few stable visual features; improve lighting, "
                "tighten the battery outline, or use a more distinctive orientation feature"
            )
        cache = _ReferenceFeatureCache(
            key=key,
            reference_image=reference,
            reference_battery=reference_battery,
            reference_size=(ref_width, ref_height),
            battery_bounds=bounds,
            detector_name=detector_name,
            scale=scale,
            keypoints=list(keypoints),
            descriptors=descriptors,
            feature_count=len(keypoints),
            orientation_mask=self._orientation_mask(
                recipe,
                reference_battery.shape[1],
                reference_battery.shape[0],
            ),
            masked_terminal_count=masked_terminal_count,
        )
        with self._lock:
            self._cache = {key: cache}
        return cache

    def readiness_issues(self, recipe: Recipe) -> list[str]:
        if not self.ready:
            return [f"BATTERY_LOCATOR_NOT_READY:{self.status}"]
        try:
            cache = self._load_reference(recipe)
        except Exception as exc:  # noqa: BLE001 - returned as a readiness gate
            return [f"BATTERY_LOCATOR_REFERENCE_INVALID:{exc}"]
        if cache.feature_count < recipe.locator_settings.minimum_matches:
            return ["BATTERY_LOCATOR_REFERENCE_FEATURES_INSUFFICIENT"]
        return []

    def locate(self, image: np.ndarray, recipe: Recipe) -> BatteryLocation:
        if image.size == 0:
            raise BatteryLocationError("Current image is empty")
        settings = recipe.locator_settings.normalized()
        cache = self._load_reference(recipe)
        detector, detector_name, norm = self._choose_detector(settings)
        if detector_name != cache.detector_name:
            with self._lock:
                self._cache.clear()
            cache = self._load_reference(recipe)

        current_small, current_scale = self._scaled(image, settings.max_detection_dimension)
        current_gray = cv2.cvtColor(current_small, cv2.COLOR_BGR2GRAY)
        current_keypoints, current_descriptors = detector.detectAndCompute(current_gray, None)
        if current_descriptors is None or len(current_keypoints) < settings.minimum_matches:
            raise BatteryLocationError("Too few visual features were found in the current image")

        matcher = cv2.BFMatcher(norm)
        pairs = matcher.knnMatch(cache.descriptors, current_descriptors, k=2)
        candidates = [
            first
            for pair in pairs
            if len(pair) == 2
            for first, second in [pair]
            if first.distance < settings.match_ratio * second.distance
        ]
        # One current feature must not vote for many reference points.
        unique_by_train: dict[int, Any] = {}
        for match in sorted(candidates, key=lambda item: float(item.distance)):
            unique_by_train.setdefault(int(match.trainIdx), match)
        good = list(unique_by_train.values())
        if len(good) < settings.minimum_matches:
            raise BatteryLocationError(
                f"Only {len(good)} reliable reference matches were found; "
                f"{settings.minimum_matches} are required"
            )

        reference_points = np.float32(
            [cache.keypoints[item.queryIdx].pt for item in good]
        ) / float(cache.scale)
        current_points = np.float32(
            [current_keypoints[item.trainIdx].pt for item in good]
        ) / float(current_scale)
        homography, inlier_mask = cv2.findHomography(
            reference_points,
            current_points,
            cv2.RANSAC,
            settings.ransac_threshold_px,
        )
        if homography is None or inlier_mask is None:
            raise BatteryLocationError("A stable battery transform could not be estimated")
        inliers = inlier_mask.reshape(-1).astype(bool)
        inlier_count = int(np.count_nonzero(inliers))
        inlier_ratio = float(inlier_count) / float(max(1, len(good)))
        if inlier_count < settings.minimum_inliers:
            raise BatteryLocationError(
                f"Battery registration produced {inlier_count} inliers; "
                f"{settings.minimum_inliers} are required"
            )
        if inlier_ratio < settings.minimum_inlier_ratio:
            raise BatteryLocationError(
                f"Battery registration inlier ratio {inlier_ratio:.1%} is below "
                f"{settings.minimum_inlier_ratio:.1%}"
            )

        projected_matches = cv2.perspectiveTransform(
            reference_points.reshape(1, -1, 2), homography
        )[0]
        errors = np.linalg.norm(projected_matches - current_points, axis=1)
        median_error = float(np.median(errors[inliers]))
        if median_error > settings.maximum_median_error_px:
            raise BatteryLocationError(
                f"Battery registration error {median_error:.2f} px exceeds "
                f"{settings.maximum_median_error_px:.2f} px"
            )

        ref_width, ref_height = cache.reference_size
        current_height, current_width = image.shape[:2]
        battery_corners_reference = _rect_corners_px(
            recipe.battery_roi,
            ref_width,
            ref_height,
        ).reshape(1, 4, 2)
        battery_corners_current = cv2.perspectiveTransform(
            battery_corners_reference,
            homography,
        )[0]
        if not cv2.isContourConvex(battery_corners_current.astype(np.float32)):
            raise BatteryLocationError("Battery registration produced a non-convex outline")

        reference_corners = battery_corners_reference[0]
        reference_top = float(np.linalg.norm(reference_corners[1] - reference_corners[0]))
        reference_left = float(np.linalg.norm(reference_corners[3] - reference_corners[0]))
        current_top = float(np.linalg.norm(battery_corners_current[1] - battery_corners_current[0]))
        current_bottom = float(np.linalg.norm(battery_corners_current[2] - battery_corners_current[3]))
        current_left = float(np.linalg.norm(battery_corners_current[3] - battery_corners_current[0]))
        current_right = float(np.linalg.norm(battery_corners_current[2] - battery_corners_current[1]))
        scale_x = ((current_top + current_bottom) / 2.0) / max(reference_top, 1.0)
        scale_y = ((current_left + current_right) / 2.0) / max(reference_left, 1.0)
        scale = math.sqrt(max(0.0, scale_x * scale_y))
        if not settings.minimum_scale <= scale <= settings.maximum_scale:
            raise BatteryLocationError(
                f"Detected battery scale {scale:.3f} is outside "
                f"{settings.minimum_scale:.3f} to {settings.maximum_scale:.3f}"
            )

        image_polygon = np.float32(
            [[0, 0], [current_width, 0], [current_width, current_height], [0, current_height]]
        )
        battery_area = float(abs(cv2.contourArea(battery_corners_current.astype(np.float32))))
        intersection_area, _intersection = cv2.intersectConvexConvex(
            battery_corners_current.astype(np.float32),
            image_polygon,
        )
        visible_fraction = float(intersection_area) / max(battery_area, 1.0)
        if visible_fraction < settings.minimum_visible_fraction:
            raise BatteryLocationError(
                f"Only {visible_fraction:.1%} of the registered battery is visible; "
                f"{settings.minimum_visible_fraction:.1%} is required"
            )

        # Reject mirrored transforms. A camera view of a rigid planar battery may
        # rotate, but it must not reflect the terminal layout.
        edge_a = battery_corners_current[1] - battery_corners_current[0]
        edge_b = battery_corners_current[3] - battery_corners_current[0]
        orientation_cross = float(edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0])
        if orientation_cross <= 0:
            raise BatteryLocationError("Battery registration appears mirrored")

        bx1, by1, bx2, by2 = cache.battery_bounds
        reference_to_local = np.array(
            [[1.0, 0.0, -float(bx1)], [0.0, 1.0, -float(by1)], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        local_to_reference = np.array(
            [[1.0, 0.0, float(bx1)], [0.0, 1.0, float(by1)], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        battery_width = max(1, bx2 - bx1)
        battery_height = max(1, by2 - by1)

        def aligned_from_transform(transform: np.ndarray) -> np.ndarray:
            try:
                inverse_transform = np.linalg.inv(transform)
            except np.linalg.LinAlgError as exc:
                raise BatteryLocationError("Battery transform is singular") from exc
            return cv2.warpPerspective(
                image,
                reference_to_local @ inverse_transform,
                (battery_width, battery_height),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(0, 0, 0),
            )

        aligned = aligned_from_transform(homography)
        reference_repr, reference_mask = self._orientation_representation(
            cache.reference_battery,
            cache.orientation_mask,
            settings.orientation_max_dimension,
        )
        aligned_repr, aligned_mask = self._orientation_representation(
            aligned,
            cache.orientation_mask,
            settings.orientation_max_dimension,
        )
        rotated_mask = cv2.rotate(reference_mask.astype(np.uint8), cv2.ROTATE_180) > 0
        common_mask = reference_mask & aligned_mask & rotated_mask
        orientation_score = self._masked_correlation(
            reference_repr,
            aligned_repr,
            common_mask,
        )
        alternate_score = self._masked_correlation(
            reference_repr,
            cv2.rotate(aligned_repr, cv2.ROTATE_180),
            common_mask,
        )

        orientation_corrected = False
        if alternate_score > orientation_score:
            local_rotation = self._rotation_matrix_180(battery_width, battery_height)
            reference_rotation = local_to_reference @ local_rotation @ reference_to_local
            homography = homography @ reference_rotation
            aligned = aligned_from_transform(homography)
            battery_corners_current = cv2.perspectiveTransform(
                battery_corners_reference,
                homography,
            )[0]
            orientation_score, alternate_score = alternate_score, orientation_score
            orientation_corrected = True

        orientation_margin = orientation_score - alternate_score
        if (
            recipe.orientation_reference != "station_direction"
            and orientation_margin < settings.minimum_orientation_margin
        ):
            raise BatteryLocationError(
                "Battery 180-degree orientation is ambiguous after terminal regions were excluded. "
                "Teach a unique case, notch, vent, or label feature, or constrain station orientation."
            )

        edge_a = battery_corners_current[1] - battery_corners_current[0]
        edge_b = battery_corners_current[3] - battery_corners_current[0]
        orientation_cross = float(edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0])
        if orientation_cross <= 0:
            raise BatteryLocationError("Battery registration appears mirrored")

        rotation_deg = math.degrees(
            math.atan2(float(edge_a[1]), float(edge_a[0]))
        )
        battery_center = np.mean(battery_corners_current, axis=0)
        perspective_skew = max(
            abs(current_top - current_bottom) / max(current_top, current_bottom, 1.0),
            abs(current_left - current_right) / max(current_left, current_right, 1.0),
        )
        metrics = {
            "method": self.status,
            "detector": detector_name,
            "reference_features": cache.feature_count,
            "current_features": len(current_keypoints),
            "good_matches": len(good),
            "inliers": inlier_count,
            "inlier_ratio": inlier_ratio,
            "median_reprojection_error_px": median_error,
            "scale": scale,
            "scale_x": scale_x,
            "scale_y": scale_y,
            "rotation_deg": rotation_deg,
            "visible_fraction": visible_fraction,
            "perspective_skew": perspective_skew,
            "orientation_score": orientation_score,
            "alternate_orientation_score": alternate_score,
            "orientation_margin": orientation_margin,
            "orientation_corrected_180": orientation_corrected,
            "terminal_regions_excluded_from_pose": cache.masked_terminal_count,
            "battery_center_normalized": [
                float(battery_center[0] / max(current_width, 1)),
                float(battery_center[1] / max(current_height, 1)),
            ],
            "homography_reference_to_current": homography.tolist(),
        }
        return BatteryLocation(
            aligned_battery=aligned,
            reference_battery=cache.reference_battery.copy(),
            battery_roi=_bounding_rect_from_polygon(
                battery_corners_current,
                current_width,
                current_height,
            ),
            battery_polygon=_polygon_normalized(
                battery_corners_current,
                current_width,
                current_height,
            ),
            reference_to_current=homography,
            reference_image_size=cache.reference_size,
            current_image_size=(current_width, current_height),
            reference_battery_bounds=cache.battery_bounds,
            metrics=metrics,
        )


class RedRingDetector:
    def detect(self, terminal_crop: np.ndarray) -> tuple[bool, float]:
        if terminal_crop.size == 0:
            return False, 0.0
        hsv = cv2.cvtColor(terminal_crop, cv2.COLOR_BGR2HSV)
        low_red = cv2.inRange(hsv, np.array([0, 75, 55]), np.array([12, 255, 255]))
        high_red = cv2.inRange(hsv, np.array([168, 75, 55]), np.array([179, 255, 255]))
        red = cv2.bitwise_or(low_red, high_red)

        height, width = red.shape
        yy, xx = np.ogrid[:height, :width]
        cx, cy = width / 2.0, height / 2.0
        distance = np.sqrt(
            ((xx - cx) / max(width, 1)) ** 2
            + ((yy - cy) / max(height, 1)) ** 2
        )
        annulus = (distance > 0.20) & (distance < 0.48)
        count = int(np.count_nonzero(annulus))
        if count == 0:
            return False, 0.0
        fraction = float(np.count_nonzero(red[annulus])) / float(count)
        present = fraction >= 0.08
        confidence = (
            min(0.999, max(0.50, fraction / 0.18))
            if present
            else min(0.999, max(0.50, 1.0 - fraction / 0.08))
        )
        return present, confidence


@dataclass(slots=True)
class TerminalFaceValidation:
    """Physical validity result for the image presented to the stamp classifier."""

    present: bool
    confidence: float
    status: str
    note: str
    metrics: dict[str, Any] = field(default_factory=dict)
    diagnostic_images: dict[str, np.ndarray] = field(default_factory=dict)


class TerminalFaceValidator:
    """Fail-closed guard that proves a terminal face exists before polarity ML.

    The marking model must never be asked to decide what an open fixture hole,
    missing threaded cap, or grossly wrong object "looks most like"—not even as
    INVALID_MARKING. This validator compares the current marking ROI
    to the *same terminal* in the accepted recipe reference using deliberately
    low-frequency, rotation-tolerant evidence.  Stamp rotation and fine surface
    scratches are suppressed; a missing central metal face is not.

    The decision uses three independent families of evidence:

    * radial luminance structure;
    * low-frequency spatial structure;
    * center color/luminance consistency against the known-good reference.

    At least two anomaly families, or a very low aggregate score, are required
    to reject the physical input.  This makes the gate conservative against
    ordinary lighting changes while still rejecting the open-hole failure mode.
    """

    status = "REFERENCE_TERMINAL_FACE_VALIDATOR_V1"
    _ANALYSIS_SIZE = 192

    @staticmethod
    def _safe_corr(left: np.ndarray, right: np.ndarray) -> float:
        a = np.asarray(left, dtype=np.float64).reshape(-1)
        b = np.asarray(right, dtype=np.float64).reshape(-1)
        if a.size != b.size or a.size < 2:
            return 0.0
        a_std = float(np.std(a))
        b_std = float(np.std(b))
        if a_std < 1e-6 or b_std < 1e-6:
            return 1.0 if float(np.mean(np.abs(a - b))) < 1.0 else 0.0
        value = float(np.corrcoef(a, b)[0, 1])
        return value if math.isfinite(value) else 0.0

    @classmethod
    def _descriptor(cls, image: np.ndarray) -> dict[str, Any]:
        if image is None or image.size == 0:
            raise ValueError("Terminal-face validation received an empty image")
        if image.ndim == 2:
            bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif image.shape[2] == 4:
            bgr = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        else:
            bgr = image[:, :, :3]
        size = cls._ANALYSIS_SIZE
        interpolation = (
            cv2.INTER_AREA
            if max(bgr.shape[:2]) >= size
            else cv2.INTER_LINEAR
        )
        resized = cv2.resize(bgr, (size, size), interpolation=interpolation)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY).astype(np.float32)
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV).astype(np.float32)

        yy, xx = np.ogrid[:size, :size]
        center = (size - 1) / 2.0
        radius = np.sqrt((xx - center) ** 2 + (yy - center) ** 2) / (size / 2.0)

        blurred = cv2.GaussianBlur(gray, (0, 0), 4.0)
        radial_profile: list[float] = []
        radial_limit = 0.88
        bins = 12
        for index in range(bins):
            inner = (index / bins) * radial_limit
            outer = ((index + 1) / bins) * radial_limit
            mask = (radius >= inner) & (radius < outer)
            radial_profile.append(float(np.mean(blurred[mask])))
        profile = np.asarray(radial_profile, dtype=np.float32)
        profile = (profile - float(np.mean(profile))) / (
            float(np.std(profile)) + 1e-6
        )

        low_frequency = cv2.resize(
            blurred, (32, 32), interpolation=cv2.INTER_AREA
        ).astype(np.float32)
        low_frequency = (low_frequency - float(np.mean(low_frequency))) / (
            float(np.std(low_frequency)) + 1e-6
        )

        center_mask = radius < 0.32
        center_saturation = float(np.median(hsv[:, :, 1][center_mask]))
        center_value = float(np.median(hsv[:, :, 2][center_mask]))
        return {
            "resized": resized,
            "radial_profile": profile,
            "low_frequency": low_frequency,
            "center_saturation": center_saturation,
            "center_value": center_value,
        }

    @staticmethod
    def _score_component(value: float, minimum: float, span: float) -> float:
        return float(np.clip((value - minimum) / max(span, 1e-6), 0.0, 1.0))

    @staticmethod
    def _diagnostic_overlay(
        image: np.ndarray,
        *,
        present: bool,
        score: float,
        status: str,
    ) -> np.ndarray:
        if image.ndim == 2:
            overlay = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif image.shape[2] == 4:
            overlay = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        else:
            overlay = image[:, :, :3].copy()
        height, width = overlay.shape[:2]
        color = (40, 150, 40) if present else (20, 20, 220)
        thickness = max(2, int(round(min(width, height) / 90.0)))
        cv2.rectangle(
            overlay,
            (1, 1),
            (max(1, width - 2), max(1, height - 2)),
            color,
            thickness,
            cv2.LINE_AA,
        )
        label = (
            f"FACE PRESENT {score:.0%}"
            if present
            else f"FACE INVALID {score:.0%}"
        )
        font_scale = max(0.45, min(1.0, min(width, height) / 360.0))
        cv2.putText(
            overlay,
            label,
            (8, max(20, int(24 * font_scale))),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            color,
            max(1, thickness - 1),
            cv2.LINE_AA,
        )
        if not present:
            cv2.putText(
                overlay,
                status.replace("TERMINAL_FACE_", ""),
                (8, max(42, int(50 * font_scale))),
                cv2.FONT_HERSHEY_SIMPLEX,
                max(0.40, font_scale * 0.75),
                color,
                max(1, thickness - 1),
                cv2.LINE_AA,
            )
        return overlay

    @staticmethod
    def _comparison_image(current: np.ndarray, reference: np.ndarray) -> np.ndarray:
        target = 256

        def prepared(image: np.ndarray) -> np.ndarray:
            if image.ndim == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            elif image.shape[2] == 4:
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
            return cv2.resize(image[:, :, :3], (target, target), interpolation=cv2.INTER_AREA)

        left = prepared(current)
        right = prepared(reference)
        cv2.putText(left, "CURRENT", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(right, "REFERENCE", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
        return np.hstack([left, right])

    def validate(
        self,
        current: np.ndarray,
        reference: np.ndarray,
        settings: MarkingClassifierSettings,
    ) -> TerminalFaceValidation:
        normalized = settings.normalized()
        if not normalized.terminal_face_validation_enabled:
            return TerminalFaceValidation(
                present=True,
                confidence=1.0,
                status="TERMINAL_FACE_VALIDATION_DISABLED",
                note="Terminal-face validation is disabled by recipe configuration.",
                metrics={
                    "terminal_face_validator": self.status,
                    "terminal_face_present": True,
                    "terminal_face_validation_enabled": False,
                },
            )
        try:
            current_desc = self._descriptor(current)
            reference_desc = self._descriptor(reference)
        except Exception as exc:  # noqa: BLE001 - fail closed on malformed evidence
            return TerminalFaceValidation(
                present=False,
                confidence=0.999,
                status="TERMINAL_FACE_INVALID",
                note=f"Terminal-face validation could not evaluate the ROI: {exc}",
                metrics={
                    "terminal_face_validator": self.status,
                    "terminal_face_present": False,
                    "terminal_face_validation_error": str(exc),
                },
            )

        radial_corr = self._safe_corr(
            current_desc["radial_profile"], reference_desc["radial_profile"]
        )
        structure_corr = self._safe_corr(
            current_desc["low_frequency"], reference_desc["low_frequency"]
        )
        saturation_delta = abs(
            float(current_desc["center_saturation"])
            - float(reference_desc["center_saturation"])
        )
        value_delta = abs(
            float(current_desc["center_value"])
            - float(reference_desc["center_value"])
        )

        radial_bad = radial_corr < normalized.terminal_face_minimum_radial_correlation
        structure_bad = (
            structure_corr < normalized.terminal_face_minimum_structure_correlation
        )
        center_color_bad = (
            saturation_delta
            > normalized.terminal_face_maximum_center_saturation_delta
            or value_delta > normalized.terminal_face_maximum_center_value_delta
        )
        anomaly_count = int(radial_bad) + int(structure_bad) + int(center_color_bad)

        radial_score = self._score_component(radial_corr, 0.05, 0.60)
        structure_score = self._score_component(structure_corr, 0.05, 0.70)
        color_score = float(
            np.clip(
                1.0
                - max(
                    saturation_delta / max(
                        normalized.terminal_face_maximum_center_saturation_delta + 20.0,
                        1.0,
                    ),
                    value_delta / max(
                        normalized.terminal_face_maximum_center_value_delta + 25.0,
                        1.0,
                    ),
                ),
                0.0,
                1.0,
            )
        )
        score = float(
            np.clip(
                0.50 * radial_score + 0.20 * structure_score + 0.30 * color_score,
                0.0,
                1.0,
            )
        )
        present = bool(
            anomaly_count <= 1 and score >= normalized.terminal_face_minimum_score
        )
        if present:
            status = "TERMINAL_FACE_PRESENT"
            confidence = max(0.50, min(0.999, score))
        else:
            # A radial mismatch plus a large center color/luminance change is
            # characteristic of the open-hole/missing-cap failure. Other
            # combinations are reported as invalid rather than overdiagnosed.
            status = (
                "TERMINAL_FACE_MISSING"
                if radial_bad and center_color_bad
                else "TERMINAL_FACE_INVALID"
            )
            confidence = max(0.50, min(0.999, 1.0 - score))

        metrics = {
            "terminal_face_validator": self.status,
            "terminal_face_present": present,
            "terminal_face_status": status,
            "terminal_face_score": score,
            "terminal_face_confidence": confidence,
            "terminal_face_radial_correlation": radial_corr,
            "terminal_face_structure_correlation": structure_corr,
            "terminal_face_center_saturation_delta": saturation_delta,
            "terminal_face_center_value_delta": value_delta,
            "terminal_face_anomaly_count": anomaly_count,
            "terminal_face_radial_gate_passed": not radial_bad,
            "terminal_face_structure_gate_passed": not structure_bad,
            "terminal_face_center_color_gate_passed": not center_color_bad,
            "terminal_face_required_radial_correlation": normalized.terminal_face_minimum_radial_correlation,
            "terminal_face_required_structure_correlation": normalized.terminal_face_minimum_structure_correlation,
            "terminal_face_max_center_saturation_delta": normalized.terminal_face_maximum_center_saturation_delta,
            "terminal_face_max_center_value_delta": normalized.terminal_face_maximum_center_value_delta,
            "terminal_face_required_score": normalized.terminal_face_minimum_score,
        }
        note = (
            f"Terminal face present; validity score {score:.1%}."
            if present
            else (
                f"{status.replace('_', ' ')}; classifier bypassed. "
                f"Validity score {score:.1%}; anomalies {anomaly_count}/3."
            )
        )
        diagnostics = {
            "terminal_face_overlay": self._diagnostic_overlay(
                current, present=present, score=score, status=status
            ),
            "terminal_face_compare": self._comparison_image(current, reference),
        }
        return TerminalFaceValidation(
            present=present,
            confidence=confidence,
            status=status,
            note=note,
            metrics=metrics,
            diagnostic_images=diagnostics,
        )


@dataclass(slots=True)
class TerminalFinishValidation:
    """Reference-anchored decision for a terminal's visible metal finish."""

    detected: TerminalFinish
    confidence: float
    evaluated: bool
    status: str
    note: str
    metrics: dict[str, Any] = field(default_factory=dict)
    diagnostic_images: dict[str, np.ndarray] = field(default_factory=dict)


class TerminalFinishValidator:
    """Conventional color check for SILVER versus BRASS terminal tops.

    This does not claim to identify metal chemistry. It verifies that the
    current terminal's robust chroma signature remains consistent with the
    accepted reference for the explicitly configured recipe finish. Dark stamp
    grooves and bright specular highlights are excluded from the comparison.
    """

    status = "REFERENCE_TERMINAL_FINISH_VALIDATOR_V1"
    maximum_pass_chroma_distance = 18.0
    maximum_pass_opposite_shift = 10.0
    minimum_mismatch_chroma_distance = 10.0
    minimum_mismatch_opposite_shift = 14.0

    @staticmethod
    def _bgr(image: np.ndarray) -> np.ndarray:
        if image is None or image.size == 0:
            raise ValueError("Terminal-finish validation received an empty image")
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        return image[:, :, :3]

    @classmethod
    def _signature(cls, image: np.ndarray) -> dict[str, float]:
        bgr = cls._bgr(image)
        size = 192
        interpolation = cv2.INTER_AREA if max(bgr.shape[:2]) >= size else cv2.INTER_LINEAR
        resized = cv2.resize(bgr, (size, size), interpolation=interpolation)
        lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)

        yy, xx = np.ogrid[:size, :size]
        center = (size - 1) / 2.0
        circle = (xx - center) ** 2 + (yy - center) ** 2 <= (size * 0.42) ** 2
        lightness = lab[:, :, 0].astype(np.float32)
        circle_values = lightness[circle]
        if circle_values.size < 64:
            raise ValueError("Terminal-finish ROI does not contain enough usable pixels")
        low, high = np.percentile(circle_values, (18.0, 88.0))
        usable = circle & (lightness >= low) & (lightness <= high)
        if int(np.count_nonzero(usable)) < 64:
            usable = circle

        a = float(np.median(lab[:, :, 1][usable]))
        b = float(np.median(lab[:, :, 2][usable]))
        saturation = float(np.median(hsv[:, :, 1][usable]))
        warmth = (b - 128.0) + 0.20 * saturation
        return {
            "lab_a": a,
            "lab_b": b,
            "saturation": saturation,
            "warmth": warmth,
            "usable_fraction": float(np.count_nonzero(usable)) / float(size * size),
        }

    @classmethod
    def _comparison_image(
        cls,
        current: np.ndarray,
        reference: np.ndarray,
        *,
        status: str,
        confidence: float,
    ) -> np.ndarray:
        target = 256

        def prepared(image: np.ndarray, label: str) -> np.ndarray:
            view = cv2.resize(cls._bgr(image), (target, target), interpolation=cv2.INTER_AREA)
            cv2.putText(
                view,
                label,
                (8, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            return view

        canvas = np.hstack(
            [prepared(current, "CURRENT"), prepared(reference, "REFERENCE")]
        )
        passed = status == "TERMINAL_FINISH_MATCH"
        color = (40, 150, 40) if passed else (20, 20, 220)
        label = status.replace("TERMINAL_FINISH_", "FINISH ").replace("_", " ")
        cv2.rectangle(canvas, (1, 1), (canvas.shape[1] - 2, canvas.shape[0] - 2), color, 3)
        cv2.putText(
            canvas,
            f"{label} {confidence:.0%}",
            (8, canvas.shape[0] - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            color,
            2,
            cv2.LINE_AA,
        )
        return canvas

    def validate(
        self,
        current: np.ndarray,
        reference: np.ndarray,
        expected: TerminalFinish,
    ) -> TerminalFinishValidation:
        if expected == TerminalFinish.UNSPECIFIED:
            return TerminalFinishValidation(
                detected=TerminalFinish.UNSPECIFIED,
                confidence=0.0,
                evaluated=False,
                status="TERMINAL_FINISH_NOT_CONFIGURED",
                note="Legacy recipe has no terminal-finish requirement.",
                metrics={"terminal_finish_validator": self.status},
            )
        try:
            current_signature = self._signature(current)
            reference_signature = self._signature(reference)
            delta_a = current_signature["lab_a"] - reference_signature["lab_a"]
            delta_b = current_signature["lab_b"] - reference_signature["lab_b"]
            chroma_distance = float(math.hypot(delta_a, delta_b))
            warmth_delta = current_signature["warmth"] - reference_signature["warmth"]
            opposite_shift = (
                warmth_delta
                if expected == TerminalFinish.SILVER
                else -warmth_delta
            )

            if (
                chroma_distance <= self.maximum_pass_chroma_distance
                and opposite_shift <= self.maximum_pass_opposite_shift
            ):
                detected = expected
                evaluated = True
                status = "TERMINAL_FINISH_MATCH"
                confidence = float(
                    np.clip(
                        1.0
                        - max(
                            chroma_distance / (self.maximum_pass_chroma_distance * 2.0),
                            max(0.0, opposite_shift)
                            / (self.maximum_pass_opposite_shift * 2.0),
                        ),
                        0.50,
                        0.999,
                    )
                )
            elif (
                chroma_distance >= self.minimum_mismatch_chroma_distance
                and opposite_shift >= self.minimum_mismatch_opposite_shift
            ):
                detected = (
                    TerminalFinish.BRASS
                    if expected == TerminalFinish.SILVER
                    else TerminalFinish.SILVER
                )
                evaluated = True
                status = "TERMINAL_FINISH_MISMATCH"
                confidence = float(
                    np.clip(
                        max(
                            chroma_distance / 30.0,
                            opposite_shift / 24.0,
                        ),
                        0.50,
                        0.999,
                    )
                )
            else:
                detected = TerminalFinish.UNSPECIFIED
                evaluated = False
                status = "TERMINAL_FINISH_NO_DECISION"
                confidence = float(
                    np.clip(
                        max(chroma_distance / 30.0, abs(opposite_shift) / 24.0),
                        0.0,
                        0.999,
                    )
                )

            metrics = {
                "terminal_finish_validator": self.status,
                "terminal_finish_expected": expected.value,
                "terminal_finish_detected": detected.value,
                "terminal_finish_status": status,
                "terminal_finish_chroma_distance": chroma_distance,
                "terminal_finish_warmth_delta": warmth_delta,
                "terminal_finish_opposite_shift": opposite_shift,
                "terminal_finish_current": current_signature,
                "terminal_finish_reference": reference_signature,
                "terminal_finish_max_pass_chroma_distance": self.maximum_pass_chroma_distance,
                "terminal_finish_max_pass_opposite_shift": self.maximum_pass_opposite_shift,
                "terminal_finish_min_mismatch_chroma_distance": self.minimum_mismatch_chroma_distance,
                "terminal_finish_min_mismatch_opposite_shift": self.minimum_mismatch_opposite_shift,
            }
            if status == "TERMINAL_FINISH_MATCH":
                note = f"Visible terminal finish matches recipe {expected.display}."
            elif status == "TERMINAL_FINISH_MISMATCH":
                note = (
                    f"Visible terminal finish appears {detected.display}; "
                    f"recipe requires {expected.display}."
                )
            else:
                note = (
                    "Terminal finish is too ambiguous for a safe SILVER/BRASS decision; "
                    "inspection fails closed."
                )
            diagnostic = self._comparison_image(
                current,
                reference,
                status=status,
                confidence=confidence,
            )
            return TerminalFinishValidation(
                detected=detected,
                confidence=confidence,
                evaluated=evaluated,
                status=status,
                note=note,
                metrics=metrics,
                diagnostic_images={"terminal_finish_compare": diagnostic},
            )
        except Exception as exc:  # noqa: BLE001 - a finish gate must fail closed
            return TerminalFinishValidation(
                detected=TerminalFinish.UNSPECIFIED,
                confidence=0.999,
                evaluated=False,
                status="TERMINAL_FINISH_NO_DECISION",
                note=f"Terminal-finish validation could not evaluate the ROI: {exc}",
                metrics={
                    "terminal_finish_validator": self.status,
                    "terminal_finish_expected": expected.value,
                    "terminal_finish_error": str(exc),
                },
            )


@dataclass(slots=True)
class MarkingClassification:
    marking: Marking
    confidence: float
    evaluated: bool
    reference_similarity: float = 0.0
    class_scores: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    note: str = ""
    diagnostic_images: dict[str, np.ndarray] = field(default_factory=dict)


class MarkingClassifier(ABC):
    ready: bool = False
    status: str = "NO_VALIDATED_MODEL"

    def readiness_issues(self, recipe: Recipe, reference_battery: np.ndarray | None = None) -> list[str]:
        del recipe, reference_battery
        return [] if self.ready else [f"POLARITY_CLASSIFIER_NOT_READY:{self.status}"]

    @abstractmethod
    def classify(
        self,
        marking_crop: np.ndarray,
        terminal: TerminalRecipe,
        recipe: Recipe,
        reference_battery: np.ndarray,
    ) -> MarkingClassification:
        raise NotImplementedError


class UnavailableMarkingClassifier(MarkingClassifier):
    ready = False
    status = "NO_VALIDATED_MODEL"

    def classify(
        self,
        marking_crop: np.ndarray,
        terminal: TerminalRecipe,
        recipe: Recipe,
        reference_battery: np.ndarray,
    ) -> MarkingClassification:
        del marking_crop, terminal, recipe, reference_battery
        return MarkingClassification(
            marking=Marking.UNREADABLE,
            confidence=0.0,
            evaluated=False,
            status=self.status,
            note="Validated polarity classifier is not loaded",
        )


class GeometricMarkingClassifier(MarkingClassifier):
    """Rotation-invariant + / - / blank recognition for terminal stamp crops.

    The classifier measures the crop itself and never consults the recipe's
    expected answer. This is a critical safety property: a reversed battery
    cannot be made to pass because the reference image or recipe says what the
    model should find. Recipes still require guided real-image validation before
    activation because stamp depth, terminal finish, and lighting vary by model.
    """

    ready = True
    status = "GEOMETRIC_STAMP_V2"

    @staticmethod
    def _classifier(recipe: Recipe) -> GeometricStampClassifier:
        settings = recipe.classifier_settings.normalized()
        return GeometricStampClassifier(
            GeometricStampThresholds(
                analysis_size_px=settings.normalized_size_px,
                minimum_contrast=settings.minimum_contrast,
                minimum_sharpness=settings.minimum_sharpness,
                maximum_clipped_fraction=settings.maximum_clipped_fraction,
                blank_maximum_signal=settings.blank_maximum_signal,
                plus_minimum_signal=settings.plus_minimum_signal,
                plus_minimum_orthogonal_ratio=settings.plus_minimum_orthogonal_ratio,
                minus_minimum_signal=settings.minus_minimum_signal,
                minus_maximum_orthogonal_ratio=settings.minus_maximum_orthogonal_ratio,
                minimum_accepted_confidence=settings.minimum_confidence,
            )
        )

    def readiness_issues(
        self,
        recipe: Recipe,
        reference_battery: np.ndarray | None = None,
    ) -> list[str]:
        del reference_battery
        if recipe.classifier_settings.normalized().method != "geometric_stamp":
            return [
                "POLARITY_CLASSIFIER_METHOD_UNSUPPORTED:"
                f"{recipe.classifier_settings.method}"
            ]
        invalid = [
            terminal.key
            for terminal in recipe.terminals
            if terminal.enabled
            and terminal.expected_marking
            not in {Marking.PLUS, Marking.MINUS, Marking.BLANK}
        ]
        if invalid:
            return ["POLARITY_CLASSIFIER_RECIPE_LABEL_INVALID:" + ",".join(invalid)]
        if not any(terminal.enabled for terminal in recipe.terminals):
            return ["POLARITY_CLASSIFIER_NO_ENABLED_TERMINALS"]
        return []

    def classify(
        self,
        marking_crop: np.ndarray,
        terminal: TerminalRecipe,
        recipe: Recipe,
        reference_battery: np.ndarray,
    ) -> MarkingClassification:
        del terminal, reference_battery
        outcome = self._classifier(recipe).classify(marking_crop)
        evaluated = True
        primary_signal = float(
            outcome.metrics.get(
                "primary_line_signal",
                outcome.metrics.get("line_signal", 0.0),
            )
        )
        note = (
            f"{outcome.status}; line={primary_signal:.3f}; "
            f"orthogonal={float(outcome.metrics.get('orthogonal_ratio', 0.0)):.3f}"
        )
        return MarkingClassification(
            marking=outcome.marking,
            confidence=outcome.confidence,
            evaluated=evaluated,
            class_scores=dict(outcome.scores),
            metrics=dict(outcome.metrics),
            status=outcome.status,
            note=note,
            diagnostic_images=dict(outcome.diagnostic_images),
        )


class OnnxMlMarkingClassifier(MarkingClassifier):
    """Primary ML classifier for isolated terminal-top polarity stamps.

    The classifier intentionally sees only the central terminal-top crop, not
    the red ring or molded case polarity symbol.  This prevents the network from
    learning those easier but unsafe shortcuts.  Terminal identity and red-ring
    inspection remain independent recipe/vision measurements.
    """

    ready = True
    status = "ONNX_ML_EXACT_CROP_V3"

    def __init__(self, model: OnnxPolarityModel) -> None:
        self.model = model

    def model_info(self, *, require_runtime: bool = False) -> dict[str, Any]:
        return self.model.info(require_runtime=require_runtime)

    @staticmethod
    def _quality(image: np.ndarray) -> dict[str, float]:
        if image.ndim == 2:
            gray = image
        else:
            gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
        contrast = float(np.std(gray))
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        clipped = float(np.mean((gray <= 2) | (gray >= 253)))
        return {
            "ml_input_contrast": contrast,
            "ml_input_sharpness": sharpness,
            "ml_input_clipped_fraction": clipped,
        }

    @staticmethod
    def _direct_ml_input_normalization(
        marking_crop: np.ndarray,
        *,
        roi_shape: str,
    ) -> TerminalTopNormalization:
        """Use the recipe marking crop *exactly* as the ONNX image source.

        This is the critical ML contract: training, offline evaluation, model
        probing, recipe validation, and production must all classify the same
        crop convention.  No second Hough-circle search or recentering step is
        allowed here.  New circle recipes already arrive as a masked square;
        legacy rectangle recipes arrive as the original taught rectangle.
        """

        if marking_crop is None or marking_crop.size == 0:
            raise ValueError("Marking ROI produced an empty ML crop")
        height, width = marking_crop.shape[:2]
        extent = float(max(1, min(width, height)))
        center_x = width / 2.0
        center_y = height / 2.0
        radius = max(1.0, extent / 2.0 - 1.0)
        overlay = marking_crop.copy()
        if overlay.ndim == 2:
            overlay = cv2.cvtColor(overlay, cv2.COLOR_GRAY2BGR)
        normalized_shape = normalize_roi_shape(roi_shape)
        if normalized_shape == CIRCLE_ROI_SHAPE:
            cv2.circle(
                overlay,
                (int(round(center_x)), int(round(center_y))),
                max(1, int(round(radius))),
                (0, 180, 255),
                max(2, int(round(extent / 120.0))),
                cv2.LINE_AA,
            )
            method = "TAUGHT_CIRCLE_DIRECT"
        else:
            cv2.rectangle(
                overlay,
                (1, 1),
                (max(1, width - 2), max(1, height - 2)),
                (0, 180, 255),
                max(1, int(round(extent / 160.0))),
                cv2.LINE_AA,
            )
            method = "LEGACY_RECT_DIRECT"
        return TerminalTopNormalization(
            crop=marking_crop.copy(),
            center_x_px=float(center_x),
            center_y_px=float(center_y),
            radius_px=float(radius),
            detection_confidence=1.0,
            method=method,
            candidate_count=0,
            crop_bounds_px=(0, 0, int(width), int(height)),
            source_width_px=int(width),
            source_height_px=int(height),
            overlay=overlay,
        )

    def readiness_issues(
        self,
        recipe: Recipe,
        reference_battery: np.ndarray | None = None,
    ) -> list[str]:
        del reference_battery
        settings = recipe.classifier_settings.normalized()
        if settings.method != "onnx_ml":
            return [f"POLARITY_CLASSIFIER_METHOD_UNSUPPORTED:{settings.method}"]
        issues = self.model.readiness_issues(require_runtime=True)
        if issues:
            return issues
        manifest = self.model.manifest
        if manifest is None:
            return ["ML_MODEL_MANIFEST_UNAVAILABLE"]
        enabled_shapes = {
            normalize_roi_shape(terminal.marking_roi_shape)
            for terminal in recipe.terminals
            if terminal.enabled
        }
        if len(enabled_shapes) > 1:
            return ["ML_RECIPE_MIXED_MARKING_ROI_SHAPES_REVALIDATE"]
        expected_crop_contract = (
            "taught_circle_masked_square_v1"
            if enabled_shapes == {CIRCLE_ROI_SHAPE}
            else "legacy_rect_v1"
        )
        model_crop_contract = str(
            manifest.metadata.get("input_crop_contract", "legacy_rect_v1")
            or "legacy_rect_v1"
        )
        if model_crop_contract != expected_crop_contract:
            return [
                "ML_MODEL_INPUT_CONTRACT_MISMATCH:"
                f"recipe={expected_crop_contract},model={model_crop_contract}"
            ]
        if not settings.ml_model_id or not settings.ml_model_sha256:
            return ["ML_MODEL_NOT_BOUND_TO_RECIPE"]
        if settings.ml_model_id != manifest.model_id:
            return [
                "ML_MODEL_ID_CHANGED_REVALIDATE_RECIPE:"
                f"{settings.ml_model_id}->{manifest.model_id}"
            ]
        if settings.ml_model_sha256.lower() != self.model.actual_sha256.lower():
            return ["ML_MODEL_HASH_CHANGED_REVALIDATE_RECIPE"]
        if (
            settings.ml_model_version
            and settings.ml_model_version != manifest.model_version
        ):
            return [
                "ML_MODEL_VERSION_CHANGED_REVALIDATE_RECIPE:"
                f"{settings.ml_model_version}->{manifest.model_version}"
            ]
        invalid = [
            terminal.key
            for terminal in recipe.terminals
            if terminal.enabled
            and terminal.expected_marking
            not in {Marking.PLUS, Marking.MINUS, Marking.BLANK}
        ]
        if invalid:
            return ["POLARITY_CLASSIFIER_RECIPE_LABEL_INVALID:" + ",".join(invalid)]
        return []

    def classify(
        self,
        marking_crop: np.ndarray,
        terminal: TerminalRecipe,
        recipe: Recipe,
        reference_battery: np.ndarray,
    ) -> MarkingClassification:
        del reference_battery
        settings = recipe.classifier_settings.normalized()
        try:
            normalized = self._direct_ml_input_normalization(
                marking_crop,
                roi_shape=terminal.marking_roi_shape,
            )
        except Exception as exc:  # noqa: BLE001 - OpenCV failure modes vary
            return MarkingClassification(
                marking=Marking.UNREADABLE,
                confidence=0.0,
                evaluated=True,
                status="ML_TERMINAL_TOP_NOT_FOUND",
                note=f"Could not isolate terminal top for ML classification: {exc}",
            )

        metrics = normalized.metrics()
        quality = self._quality(normalized.crop)
        encoded_ok, encoded = cv2.imencode(".png", normalized.crop)
        ml_input_sha256 = (
            hashlib.sha256(encoded.tobytes()).hexdigest() if encoded_ok else ""
        )
        metrics.update(quality)
        metrics.update(
            {
                "decision_mode": "onnx_ml_terminal_top",
                "classifier_engine": self.status,
                "marking_roi_shape": normalize_roi_shape(terminal.marking_roi_shape),
                "ml_input_sha256": ml_input_sha256,
                "ml_input_width_px": int(normalized.crop.shape[1]),
                "ml_input_height_px": int(normalized.crop.shape[0]),
                "ml_input_crop_contract": str(
                    self.model.info(require_runtime=False).get(
                        "input_crop_contract", "legacy_rect_v1"
                    )
                ),
            }
        )
        diagnostic_images = {
            # ``ml_input`` is the exact image passed to ONNX before resize/color
            # conversion. Keep it explicitly in evidence so commissioning can
            # compare training, probe, validation, and production byte-for-byte.
            "ml_input": normalized.crop,
            "terminal_top": normalized.crop,
            "terminal_top_overlay": normalized.overlay,
        }

        quality_bad = (
            quality["ml_input_contrast"] < settings.minimum_contrast
            or quality["ml_input_sharpness"] < settings.minimum_sharpness
            or quality["ml_input_clipped_fraction"]
            > settings.maximum_clipped_fraction
        )
        if quality_bad:
            return MarkingClassification(
                marking=Marking.UNREADABLE,
                confidence=0.0,
                evaluated=True,
                metrics=metrics,
                status="ML_IMAGE_QUALITY_GATE",
                note=(
                    "Terminal-top crop did not meet the configured contrast, "
                    "sharpness, or clipping limits."
                ),
                diagnostic_images=diagnostic_images,
            )

        try:
            inference = self.model.infer(
                normalized.crop,
                tta_quadrants=settings.ml_test_time_quadrants,
            )
        except MlModelError as exc:
            return MarkingClassification(
                marking=Marking.UNREADABLE,
                confidence=0.0,
                evaluated=False,
                metrics=metrics,
                status="ML_INFERENCE_ERROR",
                note=str(exc),
                diagnostic_images=diagnostic_images,
            )

        manifest = self.model.manifest
        metrics.update(
            {
                "ml_model_id": manifest.model_id if manifest else "",
                "ml_model_version": manifest.model_version if manifest else "",
                "ml_model_sha256": self.model.actual_sha256,
                "ml_top_label": inference.top_label,
                "ml_confidence": inference.confidence,
                "ml_margin": inference.margin,
                "ml_tta_count": inference.tta_count,
                "ml_input_size": list(inference.input_size),
                "ml_terminal_top_method": normalized.method,
            }
        )

        minimum_confidence = settings.ml_minimum_confidence
        minimum_margin = settings.ml_minimum_margin
        fallback = normalized.method == "CENTER_FALLBACK"
        if fallback:
            minimum_confidence = max(
                minimum_confidence,
                settings.ml_center_fallback_minimum_confidence,
            )
            minimum_margin = max(
                minimum_margin,
                settings.ml_center_fallback_minimum_margin,
            )
        metrics["ml_required_confidence"] = minimum_confidence
        metrics["ml_required_margin"] = minimum_margin
        metrics["ml_center_fallback"] = fallback

        class_scores = dict(inference.probabilities)
        if (
            inference.confidence < minimum_confidence
            or inference.margin < minimum_margin
        ):
            return MarkingClassification(
                marking=Marking.UNREADABLE,
                confidence=inference.confidence,
                evaluated=True,
                class_scores=class_scores,
                metrics=metrics,
                status="ML_LOW_CONFIDENCE",
                note=(
                    f"ML result {inference.top_label.upper()} did not meet "
                    f"confidence/margin gates ({inference.confidence:.3f}/"
                    f"{inference.margin:.3f})."
                ),
                diagnostic_images=diagnostic_images,
            )

        try:
            marking = Marking(inference.top_label)
        except ValueError:
            marking = Marking.UNREADABLE
        if marking == Marking.INVALID_MARKING:
            status = "ML_CLASSIFIED_INVALID_MARKING"
            note = (
                "ML model explicitly classified the observed terminal-face "
                "pattern as invalid marking."
            )
        elif marking == Marking.UNREADABLE:
            status = "ML_CLASSIFIED_UNREADABLE"
            note = "ML model explicitly classified the terminal top as unreadable."
        else:
            status = "ML_CLASS_ACCEPTED"
            note = (
                f"ML classified {marking.display} at {inference.confidence:.1%} "
                f"with margin {inference.margin:.1%}."
            )
        return MarkingClassification(
            marking=marking,
            confidence=inference.confidence,
            evaluated=True,
            class_scores=class_scores,
            metrics=metrics,
            status=status,
            note=note,
            diagnostic_images=diagnostic_images,
        )


@dataclass(slots=True)
class _MarkingFeature:
    feature: np.ndarray
    mask: np.ndarray
    contrast: float
    sharpness: float
    clipped_fraction: float
    ink_energy: float
    geometry_marking: Marking = Marking.UNREADABLE
    geometry_confidence: float = 0.0
    geometry_scores: dict[str, float] = field(default_factory=dict)
    geometry_metrics: dict[str, Any] = field(default_factory=dict)
    geometry_status: str = ""
    terminal_top_used: bool = False
    stamp_angle_deg: float | None = None
    diagnostic_images: dict[str, np.ndarray] = field(default_factory=dict)


@dataclass(slots=True)
class _MarkingTemplate:
    terminal_key: str
    marking: Marking
    feature: _MarkingFeature
    source: str = "reference"


@dataclass(slots=True)
class _TemplateMatch:
    score: float
    correlation: float
    energy_ratio: float
    rotation_deg: float
    canonical_rotation_deg: float
    shift_x_px: int
    shift_y_px: int
    mode: str


class ReferenceTemplateMarkingClassifier(MarkingClassifier):
    """Classify markings against the known-good templates in each recipe.

    This implementation is intentionally per-recipe. It handles different stamp
    fonts and terminal styles without requiring a universal neural network. The
    reference battery must be a verified known-good part, and the recipe wizard
    requires multiple real validation captures before activation.
    """

    ready = True
    status = "ROTATION_INVARIANT_HYBRID_V2_1"
    MAX_VALIDATION_TEMPLATES_PER_CLASS = 12

    def __init__(self) -> None:
        self._cache: dict[str, list[_MarkingTemplate]] = {}
        self._lock = RLock()

    @staticmethod
    def _cache_key(recipe: Recipe) -> str:
        validation_sources: list[dict[str, Any]] = []
        for record in recipe.validation_records:
            if str(record.get("disposition", "")).strip().lower() != "pass":
                continue
            record_hash = str(record.get("configuration_hash", ""))
            if (
                recipe.validation_configuration_hash
                and record_hash
                and record_hash != recipe.validation_configuration_hash
            ):
                continue
            for payload in list(record.get("terminals", []) or []):
                if not isinstance(payload, dict):
                    continue
                path_text = str(payload.get("marking_crop_path", "") or "")
                path = Path(path_text)
                stat_payload: dict[str, Any] = {
                    "terminal_key": str(payload.get("terminal_key", "")),
                    "path": path_text,
                }
                if path.is_file():
                    stat = path.stat()
                    stat_payload.update(
                        {
                            "size": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                        }
                    )
                validation_sources.append(stat_payload)
        return _safe_json_hash(
            {
                "recipe_id": recipe.recipe_id,
                "revision": recipe.revision,
                "reference_sha256": recipe.reference_image.sha256 if recipe.reference_image else "",
                "battery_roi": recipe.battery_roi.to_dict(),
                "terminals": [terminal.to_dict() for terminal in recipe.terminals],
                "settings": recipe.classifier_settings.to_dict(),
                "validation_sources": validation_sources,
            }
        )

    @staticmethod
    def _ellipse_mask(size: int) -> np.ndarray:
        yy, xx = np.ogrid[:size, :size]
        radius = np.sqrt(
            ((xx - size / 2.0) / max(size / 2.0, 1.0)) ** 2
            + ((yy - size / 2.0) / max(size / 2.0, 1.0)) ** 2
        )
        return radius < 0.47

    @staticmethod
    def _geometric_classifier(
        settings: MarkingClassifierSettings,
    ) -> GeometricStampClassifier:
        return GeometricStampClassifier(
            GeometricStampThresholds(
                analysis_size_px=settings.normalized_size_px,
                minimum_contrast=settings.minimum_contrast,
                minimum_sharpness=settings.minimum_sharpness,
                maximum_clipped_fraction=settings.maximum_clipped_fraction,
                blank_maximum_signal=settings.blank_maximum_signal,
                plus_minimum_signal=settings.plus_minimum_signal,
                plus_minimum_orthogonal_ratio=settings.plus_minimum_orthogonal_ratio,
                minus_minimum_signal=settings.minus_minimum_signal,
                minus_maximum_orthogonal_ratio=settings.minus_maximum_orthogonal_ratio,
                minimum_accepted_confidence=settings.minimum_confidence,
            )
        )

    @staticmethod
    def _terminal_top_acceptance(
        geometry: Any,
        settings: MarkingClassifierSettings,
        *,
        terminal_top_available: bool,
    ) -> tuple[bool, str, str]:
        """Choose nominal, conditional, or rejected terminal-top use.

        A technician-taught marking ROI is a search region and can be slightly
        off-center.  A real Hough circle should not be discarded solely because
        its center is not near the ROI center when the stamp itself has strong,
        centered PLUS/MINUS geometry.  Conditional acceptance is deliberately
        unavailable for BLANK because an absence decision needs the stronger
        nominal terminal-top lock.
        """

        metrics = dict(getattr(geometry, "metrics", {}) or {})
        method = str(metrics.get("terminal_top_detection_method", "") or "")
        confidence = float(metrics.get("terminal_top_detection_confidence", 0.0))
        status = str(getattr(geometry, "status", "") or "")
        marking = getattr(geometry, "marking", Marking.UNREADABLE)
        geometry_confidence = float(getattr(geometry, "confidence", 0.0))

        if not terminal_top_available:
            return False, "REJECTED", "TERMINAL_TOP_IMAGE_MISSING"
        if status in {"IMAGE_QUALITY_FAILED", "INVALID_CROP"}:
            return False, "REJECTED", status
        if method == "CENTER_FALLBACK":
            return False, "REJECTED", "CENTER_FALLBACK_NOT_ALLOWED"
        if method != "HOUGH_CIRCLE":
            return False, "REJECTED", f"UNSUPPORTED_TOP_METHOD:{method or 'NONE'}"

        if confidence >= settings.terminal_top_minimum_confidence:
            return True, "NOMINAL", "TOP_LOCK_AT_OR_ABOVE_NOMINAL_GATE"

        if confidence < settings.terminal_top_conditional_minimum_confidence:
            return False, "REJECTED", "TOP_LOCK_BELOW_CONDITIONAL_GATE"
        if marking not in {Marking.PLUS, Marking.MINUS}:
            return False, "REJECTED", "CONDITIONAL_GATE_REQUIRES_PLUS_OR_MINUS"
        if (
            geometry_confidence
            < settings.terminal_top_conditional_geometry_confidence
        ):
            return (
                False,
                "REJECTED",
                "GEOMETRY_CONFIDENCE_BELOW_CONDITIONAL_GATE",
            )

        gate_key = "plus_gate" if marking == Marking.PLUS else "minus_gate"
        center_key = (
            "plus_center_score" if marking == Marking.PLUS else "minus_center_score"
        )
        expected_status = (
            "TWO_PERPENDICULAR_LINES"
            if marking == Marking.PLUS
            else "ONE_DOMINANT_LINE"
        )
        geometry_gate = bool(metrics.get(gate_key, False))
        center_score = float(metrics.get(center_key, 0.0))
        inside_fraction = float(metrics.get("terminal_top_inside_fraction", 0.0))

        if status != expected_status:
            return False, "REJECTED", "GEOMETRY_STATUS_NOT_CONDITIONALLY_ELIGIBLE"
        if not geometry_gate:
            return False, "REJECTED", "GEOMETRY_SANITY_GATE_FAILED"
        if (
            center_score
            < settings.terminal_top_conditional_minimum_center_score
        ):
            return (
                False,
                "REJECTED",
                "STAMP_CENTER_SCORE_BELOW_CONDITIONAL_GATE",
            )
        if (
            inside_fraction
            < settings.terminal_top_conditional_minimum_inside_fraction
        ):
            return False, "REJECTED", "TERMINAL_TOP_NOT_FULLY_VISIBLE"

        return True, "CONDITIONAL", "STRONG_CENTERED_STAMP_GEOMETRY"

    @classmethod
    def _feature(
        cls,
        image: np.ndarray,
        settings: MarkingClassifierSettings,
    ) -> _MarkingFeature:
        if image.size == 0:
            raise VisionError("Marking crop is empty")

        geometry = cls._geometric_classifier(settings).classify(image)
        geometry_metrics = dict(geometry.metrics)
        terminal_top = geometry.diagnostic_images.get("terminal_top")
        terminal_top_available = bool(
            terminal_top is not None and terminal_top.size
        )
        (
            terminal_top_used,
            terminal_top_acceptance,
            terminal_top_gate_reason,
        ) = cls._terminal_top_acceptance(
            geometry,
            settings,
            terminal_top_available=terminal_top_available,
        )

        if terminal_top_used and terminal_top is not None:
            source = terminal_top
        else:
            source = image
        if source.ndim == 2:
            gray = source
        else:
            gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
        size = settings.normalized_size_px
        gray = cv2.resize(
            gray,
            (size, size),
            interpolation=(
                cv2.INTER_AREA if max(gray.shape[:2]) > size else cv2.INTER_CUBIC
            ),
        )
        gray = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(gray)
        mask = cls._ellipse_mask(size)

        blackhats: list[np.ndarray] = []
        scales = (
            (size // 30, size // 20, size // 13)
            if terminal_top_used
            else (size // 22, size // 14, size // 9)
        )
        for approximate in scales:
            kernel_size = max(5, int(approximate) | 1)
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE,
                (kernel_size, kernel_size),
            )
            blackhats.append(
                cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel).astype(
                    np.float32
                )
            )
        dark = np.maximum.reduce(blackhats)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        gradient = cv2.magnitude(gx, gy)

        def robust_normalize(values: np.ndarray) -> np.ndarray:
            active = values[mask]
            low = float(np.percentile(active, 20))
            high = float(np.percentile(active, 98))
            return np.clip(
                (values - low) / max(high - low, 1e-6),
                0.0,
                1.0,
            )

        feature = 0.65 * robust_normalize(dark) + 0.35 * robust_normalize(gradient)
        feature *= mask.astype(np.float32)
        feature = cv2.GaussianBlur(feature, (3, 3), 0)
        active_gray = gray[mask]
        clipped = float(
            np.count_nonzero((active_gray <= 3) | (active_gray >= 252))
        ) / float(max(1, active_gray.size))
        stamp_angle_value = geometry_metrics.get("stamp_angle_deg")
        try:
            stamp_angle = (
                float(stamp_angle_value)
                if stamp_angle_value is not None
                else None
            )
        except (TypeError, ValueError):
            stamp_angle = None
        geometry_metrics.update(
            {
                "terminal_top_used_by_hybrid": terminal_top_used,
                "terminal_top_acceptance": terminal_top_acceptance,
                "terminal_top_conditionally_accepted": (
                    terminal_top_acceptance == "CONDITIONAL"
                ),
                "terminal_top_gate_reason": terminal_top_gate_reason,
                "terminal_top_minimum_confidence": (
                    settings.terminal_top_minimum_confidence
                ),
                "terminal_top_conditional_minimum_confidence": (
                    settings.terminal_top_conditional_minimum_confidence
                ),
                "terminal_top_conditional_geometry_confidence": (
                    settings.terminal_top_conditional_geometry_confidence
                ),
                "terminal_top_conditional_minimum_center_score": (
                    settings.terminal_top_conditional_minimum_center_score
                ),
                "terminal_top_conditional_minimum_inside_fraction": (
                    settings.terminal_top_conditional_minimum_inside_fraction
                ),
            }
        )
        return _MarkingFeature(
            feature=feature.astype(np.float32),
            mask=mask,
            contrast=float(np.std(active_gray)),
            sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
            clipped_fraction=clipped,
            ink_energy=float(np.mean(dark[mask]) / 255.0),
            geometry_marking=geometry.marking,
            geometry_confidence=float(geometry.confidence),
            geometry_scores=dict(geometry.scores),
            geometry_metrics=geometry_metrics,
            geometry_status=geometry.status,
            terminal_top_used=terminal_top_used,
            stamp_angle_deg=stamp_angle,
            diagnostic_images=dict(geometry.diagnostic_images),
        )

    @staticmethod
    def _correlation(first: np.ndarray, second: np.ndarray, mask: np.ndarray) -> float:
        a = first[mask].astype(np.float64)
        b = second[mask].astype(np.float64)
        a -= float(np.mean(a))
        b -= float(np.mean(b))
        denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denominator <= 1e-12:
            return 0.0
        return float(np.dot(a, b) / denominator)

    @staticmethod
    def _periodic_delta(
        reference_angle: float,
        current_angle: float,
        period: float,
    ) -> float:
        half = period / 2.0
        return float(
            ((reference_angle - current_angle + half) % period) - half
        )

    @classmethod
    def _rotation_candidates(
        cls,
        reference: _MarkingFeature,
        current: _MarkingFeature,
        marking: Marking,
        settings: MarkingClassifierSettings,
    ) -> tuple[list[float], float, str]:
        max_angle = settings.maximum_residual_rotation_deg
        step = settings.rotation_step_deg
        if max_angle <= 0:
            residuals = [0.0]
        else:
            count = max(1, int(math.ceil(max_angle / step)))
            residuals = [float(index * step) for index in range(-count, count + 1)]

        canonical_delta = 0.0
        bases = [0.0]
        mode = "bounded_residual"
        if (
            reference.terminal_top_used
            and current.terminal_top_used
            and reference.stamp_angle_deg is not None
            and current.stamp_angle_deg is not None
            and marking in {Marking.PLUS, Marking.MINUS}
        ):
            period = 90.0 if marking == Marking.PLUS else 180.0
            canonical_delta = cls._periodic_delta(
                reference.stamp_angle_deg,
                current.stamp_angle_deg,
                period,
            )
            # Test both signs because image-coordinate line angles and
            # warpAffine rotations use opposite visual conventions. This still
            # searches only around the measured stamp orientation, not 180°.
            bases = [canonical_delta, -canonical_delta]
            mode = "stamp_angle_canonical"

        angles: list[float] = []
        for base in bases:
            for residual in residuals:
                candidate = float(base + residual)
                if not any(abs(candidate - item) < 1e-6 for item in angles):
                    angles.append(candidate)
        return angles or [0.0], canonical_delta, mode

    @classmethod
    def _best_similarity(
        cls,
        reference: _MarkingFeature,
        current: _MarkingFeature,
        marking: Marking,
        settings: MarkingClassifierSettings,
    ) -> _TemplateMatch:
        size = settings.normalized_size_px
        angles, canonical_delta, mode = cls._rotation_candidates(
            reference,
            current,
            marking,
            settings,
        )
        center = (size / 2.0, size / 2.0)
        max_shift = max(0, int(settings.maximum_shift_px))
        match_mask = reference.mask.astype(np.uint8) * 255
        energy_ratio = (
            min(reference.ink_energy, current.ink_energy)
            / max(reference.ink_energy, current.ink_energy, 1e-6)
        )
        best = _TemplateMatch(
            score=0.0,
            correlation=0.0,
            energy_ratio=float(energy_ratio),
            rotation_deg=0.0,
            canonical_rotation_deg=float(canonical_delta),
            shift_x_px=0,
            shift_y_px=0,
            mode=mode,
        )
        for angle in angles:
            rotation = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated = cv2.warpAffine(
                current.feature,
                rotation,
                (size, size),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT,
            )
            if max_shift:
                search = cv2.copyMakeBorder(
                    rotated,
                    max_shift,
                    max_shift,
                    max_shift,
                    max_shift,
                    cv2.BORDER_REFLECT,
                )
            else:
                search = rotated
            correlation_map = cv2.matchTemplate(
                search,
                reference.feature,
                cv2.TM_CCOEFF_NORMED,
                mask=match_mask,
            )
            finite = np.nan_to_num(
                correlation_map,
                nan=-1.0,
                posinf=-1.0,
                neginf=-1.0,
            )
            _, maximum, _, location = cv2.minMaxLoc(finite)
            correlation = max(0.0, float(maximum))
            score = float(
                min(1.0, max(0.0, 0.88 * correlation + 0.12 * energy_ratio))
            )
            if score > best.score:
                best = _TemplateMatch(
                    score=score,
                    correlation=correlation,
                    energy_ratio=float(energy_ratio),
                    rotation_deg=float(angle),
                    canonical_rotation_deg=float(canonical_delta),
                    shift_x_px=int(location[0] - max_shift),
                    shift_y_px=int(location[1] - max_shift),
                    mode=mode,
                )
        return best

    def _templates(
        self,
        recipe: Recipe,
        reference_battery: np.ndarray,
    ) -> list[_MarkingTemplate]:
        key = self._cache_key(recipe)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        settings = recipe.classifier_settings.normalized()
        templates: list[_MarkingTemplate] = []
        for terminal in recipe.terminals:
            if not terminal.enabled:
                continue
            terminal_crop = crop_normalized(reference_battery, terminal.search_roi)
            marking_crop, _effective_marking_roi = crop_marking_region(
                terminal_crop,
                terminal.marking_roi,
                terminal.marking_roi_shape,
            )
            templates.append(
                _MarkingTemplate(
                    terminal_key=terminal.key,
                    marking=terminal.expected_marking,
                    feature=self._feature(marking_crop, settings),
                    source="reference",
                )
            )

        # Successful guided validation captures become additional examples for
        # the same recipe class. This makes the template classifier tolerant of
        # ordinary position, lighting, and lot variation without training a
        # universal neural network. Only PASS records from the current recipe
        # fingerprint are eligible, and missing evidence is ignored rather than
        # silently blocking an otherwise valid reference template.
        terminal_by_key = {
            terminal.key: terminal
            for terminal in recipe.terminals
            if terminal.enabled
        }
        validation_count_by_class: dict[Marking, int] = {}
        seen_paths: set[Path] = set()
        for record in recipe.validation_records:
            if str(record.get("disposition", "")).strip().lower() != "pass":
                continue
            record_hash = str(record.get("configuration_hash", ""))
            if (
                recipe.validation_configuration_hash
                and record_hash
                and record_hash != recipe.validation_configuration_hash
            ):
                continue
            for payload in list(record.get("terminals", []) or []):
                if not isinstance(payload, dict):
                    continue
                terminal_key = str(payload.get("terminal_key", ""))
                terminal = terminal_by_key.get(terminal_key)
                if terminal is None:
                    continue
                path = Path(str(payload.get("marking_crop_path", "") or ""))
                if not path.is_file() or path in seen_paths:
                    continue
                label = terminal.expected_marking
                if (
                    validation_count_by_class.get(label, 0)
                    >= self.MAX_VALIDATION_TEMPLATES_PER_CLASS
                ):
                    continue
                sample = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if sample is None or sample.size == 0:
                    continue
                templates.append(
                    _MarkingTemplate(
                        terminal_key=terminal.key,
                        marking=label,
                        feature=self._feature(sample, settings),
                        source="validation",
                    )
                )
                seen_paths.add(path)
                validation_count_by_class[label] = (
                    validation_count_by_class.get(label, 0) + 1
                )
        with self._lock:
            if len(self._cache) >= 12 and key not in self._cache:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = templates
        return templates

    def readiness_issues(self, recipe: Recipe, reference_battery: np.ndarray | None = None) -> list[str]:
        if not self.ready:
            return [f"POLARITY_CLASSIFIER_NOT_READY:{self.status}"]
        if any(
            terminal.enabled
            and terminal.expected_marking not in {Marking.PLUS, Marking.MINUS, Marking.BLANK}
            for terminal in recipe.terminals
        ):
            return ["POLARITY_CLASSIFIER_RECIPE_LABEL_INVALID"]
        if reference_battery is None:
            if not recipe.reference_image or not Path(recipe.reference_image.path).is_file():
                return ["POLARITY_CLASSIFIER_REFERENCE_MISSING"]
            reference = cv2.imread(str(recipe.reference_image.path), cv2.IMREAD_COLOR)
            if reference is None:
                return ["POLARITY_CLASSIFIER_REFERENCE_UNREADABLE"]
            reference_battery = crop_normalized(reference, recipe.battery_roi)
        try:
            templates = self._templates(recipe, reference_battery)
        except Exception as exc:  # noqa: BLE001
            return [f"POLARITY_CLASSIFIER_REFERENCE_INVALID:{exc}"]
        if not templates:
            return ["POLARITY_CLASSIFIER_NO_ENABLED_TERMINALS"]
        taught_classes = {item.marking for item in templates}
        if len(taught_classes) < 2:
            return [
                "POLARITY_CLASSIFIER_NEEDS_TWO_TAUGHT_CLASSES:"
                + ",".join(sorted(item.value for item in taught_classes))
            ]
        settings = recipe.classifier_settings.normalized()
        poor = [
            item.terminal_key
            for item in templates
            if (
                item.marking != Marking.BLANK
                and item.feature.contrast < settings.minimum_contrast
            )
            or item.feature.sharpness < settings.minimum_sharpness
            or item.feature.clipped_fraction > settings.maximum_clipped_fraction
        ]
        if poor:
            return ["POLARITY_CLASSIFIER_REFERENCE_CROP_POOR:" + ",".join(poor)]
        return []

    def classify(
        self,
        marking_crop: np.ndarray,
        terminal: TerminalRecipe,
        recipe: Recipe,
        reference_battery: np.ndarray,
    ) -> MarkingClassification:
        settings = recipe.classifier_settings.normalized()
        current = self._feature(marking_crop, settings)

        def make_classification(**kwargs: Any) -> MarkingClassification:
            kwargs.setdefault(
                "diagnostic_images",
                dict(current.diagnostic_images),
            )
            return MarkingClassification(**kwargs)

        current_metrics: dict[str, Any] = {
            "contrast": current.contrast,
            "sharpness": current.sharpness,
            "clipped_fraction": current.clipped_fraction,
            "ink_energy": current.ink_energy,
            "geometry_marking": current.geometry_marking.value,
            "geometry_confidence": current.geometry_confidence,
            "geometry_status": current.geometry_status,
            "geometry_scores": dict(current.geometry_scores),
            "terminal_top_used": current.terminal_top_used,
            "stamp_angle_deg": current.stamp_angle_deg,
            **dict(current.geometry_metrics),
        }
        if (
            current.sharpness < settings.minimum_sharpness
            or current.clipped_fraction > settings.maximum_clipped_fraction
        ):
            return make_classification(
                marking=Marking.UNREADABLE,
                confidence=0.0,
                evaluated=True,
                metrics=current_metrics,
                status="IMAGE_QUALITY_FAILED",
                note=(
                    "Marking crop failed readability checks: "
                    f"contrast={current.contrast:.2f}, "
                    f"sharpness={current.sharpness:.2f}, "
                    f"clipped={current.clipped_fraction:.1%}"
                ),
            )

        templates = self._templates(recipe, reference_battery)
        template_scores: dict[Marking, float] = {}
        template_count_by_class: dict[Marking, int] = {}
        best_match_by_class: dict[Marking, _TemplateMatch] = {}
        same_terminal_similarity = 0.0
        same_terminal_match: _TemplateMatch | None = None
        for template in templates:
            match = self._best_similarity(
                template.feature,
                current,
                template.marking,
                settings,
            )
            if match.score > template_scores.get(template.marking, -1.0):
                template_scores[template.marking] = match.score
                best_match_by_class[template.marking] = match
            template_count_by_class[template.marking] = (
                template_count_by_class.get(template.marking, 0) + 1
            )
            if (
                template.terminal_key == terminal.key
                and match.score > same_terminal_similarity
            ):
                same_terminal_similarity = match.score
                same_terminal_match = match

        template_ordered = sorted(
            template_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        if not template_ordered:
            return make_classification(
                marking=Marking.UNREADABLE,
                confidence=0.0,
                evaluated=False,
                metrics=current_metrics,
                status="NO_REFERENCE_TEMPLATES",
                note="No reference marking templates are available",
            )

        def match_payload(match: _TemplateMatch | None) -> dict[str, Any]:
            if match is None:
                return {}
            return {
                "score": match.score,
                "correlation": match.correlation,
                "energy_ratio": match.energy_ratio,
                "rotation_deg": match.rotation_deg,
                "canonical_rotation_deg": match.canonical_rotation_deg,
                "shift_x_px": match.shift_x_px,
                "shift_y_px": match.shift_y_px,
                "mode": match.mode,
            }

        template_top_marking, template_top_score = template_ordered[0]
        template_second_score = (
            template_ordered[1][1] if len(template_ordered) > 1 else 0.0
        )
        template_margin = template_top_score - template_second_score
        required_score = max(
            settings.acceptance_threshold,
            settings.minimum_confidence,
        )
        current_metrics.update(
            {
                "template_count": len(templates),
                "template_counts": {
                    marking.value: count
                    for marking, count in sorted(
                        template_count_by_class.items(),
                        key=lambda item: item[0].value,
                    )
                },
                "template_scores": {
                    marking.value: float(score)
                    for marking, score in template_ordered
                },
                "template_top_class": template_top_marking.value,
                "template_top_score": float(template_top_score),
                "template_second_score": float(template_second_score),
                "template_score_margin": float(template_margin),
                "same_terminal_similarity": float(same_terminal_similarity),
                "same_terminal_match": match_payload(same_terminal_match),
                "best_template_matches": {
                    marking.value: match_payload(match)
                    for marking, match in best_match_by_class.items()
                },
                "required_score": float(required_score),
                "minimum_margin": settings.minimum_margin,
            }
        )

        geometry_eligible = bool(
            current.terminal_top_used
            and current.geometry_marking
            in {Marking.PLUS, Marking.MINUS, Marking.BLANK}
            and current.geometry_confidence >= settings.minimum_confidence
        )
        taught_classes = set(template_scores)

        if geometry_eligible:
            geometry_marking = current.geometry_marking
            confirmation = template_scores.get(geometry_marking, 0.0)
            conflict = bool(
                template_top_marking != geometry_marking
                and template_top_score
                >= settings.hybrid_conflict_template_threshold
                and template_margin >= settings.minimum_margin
            )
            current_metrics.update(
                {
                    "decision_mode": "rotation_invariant_hybrid",
                    "hybrid_geometry_weight": settings.hybrid_geometry_weight,
                    "hybrid_template_weight": 1.0
                    - settings.hybrid_geometry_weight,
                    "geometry_template_confirmation": float(confirmation),
                    "minimum_template_confirmation": (
                        settings.hybrid_minimum_template_confirmation
                    ),
                    "template_geometry_conflict": conflict,
                }
            )

            if conflict:
                return make_classification(
                    marking=Marking.UNREADABLE,
                    confidence=max(
                        current.geometry_confidence, template_top_score
                    ),
                    evaluated=True,
                    reference_similarity=same_terminal_similarity,
                    class_scores={
                        marking.value: float(score)
                        for marking, score in template_ordered
                    },
                    metrics=current_metrics,
                    status="HYBRID_CLASSIFIER_CONFLICT",
                    note=(
                        f"Stamp geometry indicates {geometry_marking.display}, "
                        f"but a taught {template_top_marking.display} template "
                        f"matched strongly at {template_top_score:.3f}"
                    ),
                )

            # An independently detected class that is absent from the recipe is
            # still useful evidence of a wrong product. It cannot pass because
            # no enabled terminal expects an untaught class.
            if geometry_marking not in taught_classes:
                scores = {
                    marking.value: float(score)
                    for marking, score in template_ordered
                }
                scores[geometry_marking.value] = float(
                    current.geometry_confidence
                )
                current_metrics["hybrid_scores"] = dict(scores)
                return make_classification(
                    marking=geometry_marking,
                    confidence=current.geometry_confidence,
                    evaluated=True,
                    reference_similarity=same_terminal_similarity,
                    class_scores=scores,
                    metrics=current_metrics,
                    status="UNTAUGHT_GEOMETRY_CLASS",
                    note=(
                        f"{geometry_marking.display} stamp geometry detected, "
                        "but that class is not taught in this recipe"
                    ),
                )

            if confirmation < settings.hybrid_minimum_template_confirmation:
                return make_classification(
                    marking=Marking.UNREADABLE,
                    confidence=current.geometry_confidence,
                    evaluated=True,
                    reference_similarity=same_terminal_similarity,
                    class_scores={
                        marking.value: float(score)
                        for marking, score in template_ordered
                    },
                    metrics=current_metrics,
                    status="GEOMETRY_UNCONFIRMED",
                    note=(
                        f"{geometry_marking.display} geometry was strong, but "
                        f"template confirmation {confirmation:.3f} is below "
                        f"{settings.hybrid_minimum_template_confirmation:.3f}"
                    ),
                )

            geometry_weight = settings.hybrid_geometry_weight
            template_weight = 1.0 - geometry_weight
            hybrid_scores: dict[Marking, float] = {}
            for marking in set(template_scores) | {geometry_marking}:
                geometry_score = (
                    current.geometry_confidence
                    if marking == geometry_marking
                    else 0.0
                )
                hybrid_scores[marking] = float(
                    geometry_weight * geometry_score
                    + template_weight * template_scores.get(marking, 0.0)
                )
            hybrid_ordered = sorted(
                hybrid_scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            hybrid_top_marking, hybrid_top_score = hybrid_ordered[0]
            hybrid_second_score = (
                hybrid_ordered[1][1] if len(hybrid_ordered) > 1 else 0.0
            )
            hybrid_margin = hybrid_top_score - hybrid_second_score
            score_payload = {
                marking.value: float(score)
                for marking, score in hybrid_ordered
            }
            current_metrics.update(
                {
                    "hybrid_scores": dict(score_payload),
                    "top_class": hybrid_top_marking.value,
                    "top_score": float(hybrid_top_score),
                    "second_score": float(hybrid_second_score),
                    "score_margin": float(hybrid_margin),
                }
            )

            if (
                hybrid_top_marking == geometry_marking
                and hybrid_top_score >= required_score
                and hybrid_margin >= settings.minimum_margin
            ):
                if (
                    hybrid_top_marking != Marking.BLANK
                    and current.contrast < settings.minimum_contrast
                ):
                    return make_classification(
                        marking=Marking.UNREADABLE,
                        confidence=hybrid_top_score,
                        evaluated=True,
                        reference_similarity=same_terminal_similarity,
                        class_scores=score_payload,
                        metrics=current_metrics,
                        status="LOW_CONTRAST_NONBLANK",
                        note=(
                            f"Geometry indicated {hybrid_top_marking.display}, "
                            f"but contrast {current.contrast:.2f} is below "
                            f"{settings.minimum_contrast:.2f}"
                        ),
                    )
                angle_text = (
                    f" at {current.stamp_angle_deg:.1f} degrees"
                    if current.stamp_angle_deg is not None
                    else ""
                )
                top_lock = float(
                    current.geometry_metrics.get(
                        "terminal_top_detection_confidence", 0.0
                    )
                )
                top_acceptance = str(
                    current.geometry_metrics.get(
                        "terminal_top_acceptance", "NOMINAL"
                    )
                ).lower()
                return make_classification(
                    marking=hybrid_top_marking,
                    confidence=hybrid_top_score,
                    evaluated=True,
                    reference_similarity=same_terminal_similarity,
                    class_scores=score_payload,
                    metrics=current_metrics,
                    status="HYBRID_CLASS_ACCEPTED",
                    note=(
                        f"{hybrid_top_marking.display} stamp geometry"
                        f"{angle_text}; top lock {top_lock:.1%} "
                        f"({top_acceptance}); "
                        f"template confirmation {confirmation:.3f}; "
                        f"hybrid {hybrid_top_score:.3f}"
                    ),
                )

            return make_classification(
                marking=Marking.UNREADABLE,
                confidence=hybrid_top_score,
                evaluated=True,
                reference_similarity=same_terminal_similarity,
                class_scores=score_payload,
                metrics=current_metrics,
                status="HYBRID_CLASS_AMBIGUOUS",
                note=(
                    f"Hybrid evidence was ambiguous: "
                    f"{hybrid_top_marking.display} {hybrid_top_score:.3f}, "
                    f"margin {hybrid_margin:.3f}"
                ),
            )

        # Compatibility path for terminal families where the central top cannot
        # be confidently isolated. This retains the original fail-closed
        # reference-template decision.
        top_marking = template_top_marking
        top_score = template_top_score
        second_score = template_second_score
        margin = template_margin
        score_payload = {
            marking.value: float(score)
            for marking, score in template_ordered
        }
        current_metrics.update(
            {
                "decision_mode": "reference_template_fallback",
                "top_class": top_marking.value,
                "top_score": float(top_score),
                "second_score": float(second_score),
                "score_margin": float(margin),
            }
        )

        if top_score >= required_score and margin >= settings.minimum_margin:
            if (
                top_marking != Marking.BLANK
                and current.contrast < settings.minimum_contrast
            ):
                return make_classification(
                    marking=Marking.UNREADABLE,
                    confidence=top_score,
                    evaluated=True,
                    reference_similarity=same_terminal_similarity,
                    class_scores=score_payload,
                    metrics=current_metrics,
                    status="LOW_CONTRAST_NONBLANK",
                    note=(
                        f"Best class was {top_marking.display}, but contrast "
                        f"{current.contrast:.2f} is below "
                        f"{settings.minimum_contrast:.2f}"
                    ),
                )
            return make_classification(
                marking=top_marking,
                confidence=top_score,
                evaluated=True,
                reference_similarity=same_terminal_similarity,
                class_scores=score_payload,
                metrics=current_metrics,
                status="CLASS_ACCEPTED",
                note=(
                    f"Reference-template fallback match {top_score:.3f}; "
                    f"margin {margin:.3f}; same-terminal "
                    f"{same_terminal_similarity:.3f}"
                ),
            )

        if current.contrast < settings.minimum_contrast:
            return make_classification(
                marking=Marking.UNREADABLE,
                confidence=top_score,
                evaluated=True,
                reference_similarity=same_terminal_similarity,
                class_scores=score_payload,
                metrics=current_metrics,
                status="LOW_CONTRAST_UNRESOLVED",
                note=(
                    f"No accepted BLANK match and contrast "
                    f"{current.contrast:.2f} is below "
                    f"{settings.minimum_contrast:.2f}"
                ),
            )

        if top_score < required_score:
            return make_classification(
                marking=Marking.OTHER,
                confidence=max(0.0, 1.0 - top_score),
                evaluated=True,
                reference_similarity=same_terminal_similarity,
                class_scores=score_payload,
                metrics=current_metrics,
                status="NO_TAUGHT_CLASS_MATCH",
                note=(
                    f"No taught marking reached threshold "
                    f"{required_score:.3f}; best was "
                    f"{top_marking.display} at {top_score:.3f}"
                ),
            )
        return make_classification(
            marking=Marking.UNREADABLE,
            confidence=top_score,
            evaluated=True,
            reference_similarity=same_terminal_similarity,
            class_scores=score_payload,
            metrics=current_metrics,
            status="CLASS_AMBIGUOUS",
            note=(
                f"Marking is ambiguous: {top_marking.display} score "
                f"{top_score:.3f}, margin {margin:.3f} below "
                f"{settings.minimum_margin:.3f}"
            ),
        )


class RecipeConfiguredMarkingClassifier(MarkingClassifier):
    """Dispatch to the recipe-selected classifier implementation.

    The HMI and inspection controller operate against one stable interface while
    each recipe can select the ONNX ML implementation, the conservative legacy
    reference-template implementation, or the engineering geometric
    implementation. Unsupported values fail closed during readiness.
    """

    ready = True
    status = "RECIPE_CONFIGURED_CLASSIFIER"

    def __init__(
        self,
        *,
        reference_template: ReferenceTemplateMarkingClassifier | None = None,
        geometric: GeometricMarkingClassifier | None = None,
        onnx_ml: OnnxMlMarkingClassifier | None = None,
    ) -> None:
        self.reference_template = reference_template or ReferenceTemplateMarkingClassifier()
        self.geometric = geometric or GeometricMarkingClassifier()
        self.onnx_ml = onnx_ml

    def _selected(self, recipe: Recipe) -> MarkingClassifier:
        method = recipe.classifier_settings.normalized().method
        if method == "reference_template":
            return self.reference_template
        if method == "geometric_stamp":
            return self.geometric
        if method == "onnx_ml":
            if self.onnx_ml is None:
                raise VisionError("ONNX ML model is not configured on this station")
            return self.onnx_ml
        raise VisionError(f"Unsupported marking classifier method: {method}")

    def ml_model_info(self, *, require_runtime: bool = False) -> dict[str, Any]:
        if self.onnx_ml is None:
            return {
                "ready": False,
                "issues": ["ML_MODEL_NOT_CONFIGURED"],
                "model_id": "",
                "model_version": "",
                "model_sha256": "",
                "classes": [],
                "input_size": [],
            }
        return self.onnx_ml.model_info(require_runtime=require_runtime)

    def status_for_recipe(self, recipe: Recipe | None) -> str:
        if recipe is None:
            return self.status
        try:
            return self._selected(recipe).status
        except VisionError:
            return "UNSUPPORTED_CLASSIFIER_METHOD"

    def readiness_issues(
        self,
        recipe: Recipe,
        reference_battery: np.ndarray | None = None,
    ) -> list[str]:
        try:
            selected = self._selected(recipe)
        except VisionError as exc:
            return [f"POLARITY_CLASSIFIER_METHOD_UNSUPPORTED:{exc}"]
        return selected.readiness_issues(recipe, reference_battery)

    def classify(
        self,
        marking_crop: np.ndarray,
        terminal: TerminalRecipe,
        recipe: Recipe,
        reference_battery: np.ndarray,
    ) -> MarkingClassification:
        return self._selected(recipe).classify(
            marking_crop,
            terminal,
            recipe,
            reference_battery,
        )


class InspectionPipeline:
    def __init__(
        self,
        *,
        output_directory: Path,
        battery_locator: BatteryLocator | None = None,
        ring_detector: RedRingDetector | None = None,
        terminal_face_validator: TerminalFaceValidator | None = None,
        terminal_finish_validator: TerminalFinishValidator | None = None,
        marking_classifier: MarkingClassifier | None = None,
        ml_model: OnnxPolarityModel | None = None,
        failure_retention_policy: FailureRetentionPolicy | None = None,
    ) -> None:
        self.output_directory = output_directory
        self.battery_locator = battery_locator or ReferenceFeatureBatteryLocator()
        self.ring_detector = ring_detector or RedRingDetector()
        self.terminal_face_validator = terminal_face_validator or TerminalFaceValidator()
        self.terminal_finish_validator = (
            terminal_finish_validator or TerminalFinishValidator()
        )
        self.marking_classifier = marking_classifier or RecipeConfiguredMarkingClassifier(
            onnx_ml=(OnnxMlMarkingClassifier(ml_model) if ml_model is not None else None)
        )
        self.failure_retention_policy = (
            failure_retention_policy or FailureRetentionPolicy()
        )
        self._retention_lock = RLock()

    def set_failure_retention_policy(self, policy: FailureRetentionPolicy) -> None:
        with self._retention_lock:
            self.failure_retention_policy = policy

    def apply_failure_retention(self) -> FailureRetentionReport:
        with self._retention_lock:
            try:
                return apply_failure_retention(
                    self.output_directory / "inspections",
                    self.failure_retention_policy,
                )
            except OSError:
                # Evidence is already safely written at this point. A locked folder
                # must not change the inspection/PLC decision; maintenance can retry
                # retention on the next cycle or startup.
                return FailureRetentionReport()

    def ml_model_info(self, *, require_runtime: bool = False) -> dict[str, Any]:
        getter = getattr(self.marking_classifier, "ml_model_info", None)
        if callable(getter):
            return dict(getter(require_runtime=require_runtime))
        return {
            "ready": False,
            "issues": ["ML_MODEL_NOT_AVAILABLE_FOR_CLASSIFIER"],
            "model_id": "",
            "model_version": "",
            "model_sha256": "",
            "classes": [],
            "input_size": [],
        }

    def classifier_status_for_recipe(self, recipe: Recipe | None) -> str:
        status_for_recipe = getattr(self.marking_classifier, "status_for_recipe", None)
        if callable(status_for_recipe):
            return str(status_for_recipe(recipe))
        return self.marking_classifier.status

    def readiness_issues(
        self,
        recipe: Recipe | None,
        *,
        validation_mode: bool = False,
    ) -> list[str]:
        if recipe is None:
            return ["NO_ACTIVE_RECIPE"]
        issues: list[str] = []
        if not recipe.has_reference_image:
            issues.append("RECIPE_REFERENCE_IMAGE_REQUIRED")
        elif recipe.reference_image is None or not Path(recipe.reference_image.path).is_file():
            issues.append("RECIPE_REFERENCE_FILE_MISSING")
        elif recipe.reference_is_simulated and not recipe.reference_is_demo:
            issues.append("SIMULATED_REFERENCE_NOT_ALLOWED")
        if not validation_mode and not recipe.validation_complete:
            required_validation = max(1, int(recipe.validation_runs_required))
            issues.append(
                "RECIPE_VALIDATION_REQUIRED:"
                f"{recipe.validation_pass_record_count}/{required_validation}:"
                f"{INSPECTION_ENGINE}"
            )
        if recipe.has_reference_image:
            issues.extend(self.battery_locator.readiness_issues(recipe))
            issues.extend(self.marking_classifier.readiness_issues(recipe))
        elif not self.battery_locator.ready:
            issues.append(f"BATTERY_LOCATOR_NOT_READY:{self.battery_locator.status}")
        elif not self.marking_classifier.ready:
            issues.append(f"POLARITY_CLASSIFIER_NOT_READY:{self.marking_classifier.status}")
        return list(dict.fromkeys(issues))

    @staticmethod
    def _coerce_frame(frame: CameraFrame | np.ndarray) -> CameraFrame:
        if isinstance(frame, CameraFrame):
            return frame
        now = datetime.now(timezone.utc).isoformat()
        tick = monotonic_ns()
        return CameraFrame(
            image=frame,
            sequence=1,
            frame_id=f"LEGACY-{uuid4().hex[:12]}",
            requested_at_utc=now,
            captured_at_utc=now,
            request_monotonic_ns=tick,
            captured_monotonic_ns=tick,
            backend_name="legacy-array",
        )

    @staticmethod
    def _reason_for_readiness(issues: list[str]) -> str:
        if "NO_ACTIVE_RECIPE" in issues:
            return "NO ACTIVE RECIPE"
        if any(item.startswith("RECIPE_REFERENCE_IMAGE_REQUIRED") for item in issues):
            return "RECIPE REFERENCE IMAGE REQUIRED"
        if any(item.startswith("RECIPE_REFERENCE_FILE_MISSING") for item in issues):
            return "RECIPE REFERENCE IMAGE FILE MISSING"
        if any(item.startswith("SIMULATED_REFERENCE_NOT_ALLOWED") for item in issues):
            return "CAPTURE THE RECIPE REFERENCE WITH A PHYSICAL CAMERA"
        if any(item.startswith("RECIPE_VALIDATION_REQUIRED") for item in issues):
            return "RECIPE VALIDATION REQUIRED"
        if any(item.startswith("BATTERY_LOCATOR_REFERENCE") for item in issues):
            return "BATTERY LOCATOR REFERENCE IS NOT USABLE"
        if any(item.startswith("POLARITY_CLASSIFIER_REFERENCE") for item in issues):
            return "POLARITY REFERENCE CROPS ARE NOT USABLE"
        return "INSPECTION NOT READY — " + "; ".join(issues)

    def inspect(
        self,
        frame: CameraFrame | np.ndarray,
        recipe: Recipe | None,
        *,
        trigger_source: str = "PLC",
        cycle_id: str = "",
        stage_callback: StageCallback | None = None,
        validation_mode: bool = False,
    ) -> InspectionResult:
        started = perf_counter()
        captured = self._coerce_frame(frame)
        if not captured.fresh:
            raise VisionError("The camera frame failed freshness validation")
        if captured.image.size == 0:
            raise VisionError("The camera returned an empty image")

        cycle_id = cycle_id or f"CYCLE-{uuid4()}"
        capture_id = str(uuid4())
        quality = assess_image_quality(captured.image)

        configuration_issues = self.readiness_issues(
            recipe,
            validation_mode=validation_mode,
        )
        classifier_readiness_issues = [
            issue
            for issue in configuration_issues
            if issue.startswith("ML_")
            or issue.startswith("POLARITY_CLASSIFIER")
        ]
        if str(quality.get("status", "")).upper() == "POOR":
            configuration_issues.append(
                f"IMAGE_QUALITY_POOR:{quality.get('reason', 'UNKNOWN')}"
            )

        location: BatteryLocation | None = None
        locator_error = ""
        terminal_results: list[TerminalInspection] = []
        battery_roi = (
            recipe.battery_roi
            if recipe is not None
            else NormalizedRect(0.0, 0.0, 1.0, 1.0)
        )
        battery_polygon: list[tuple[float, float]] = []
        locator_metrics: dict[str, Any] = {}

        if recipe is not None and not any(
            issue.startswith("RECIPE_REFERENCE")
            or issue.startswith("SIMULATED_REFERENCE")
            for issue in configuration_issues
        ):
            if stage_callback:
                stage_callback(InspectionCycleState.LOCATING, "Registering battery to recipe reference")
            try:
                location = self.battery_locator.locate(captured.image, recipe)
                battery_roi = location.battery_roi
                battery_polygon = list(location.battery_polygon)
                locator_metrics = dict(location.metrics)
            except Exception as exc:  # noqa: BLE001 - converted to result evidence
                locator_error = str(exc)

        if recipe is not None and location is not None:
            if stage_callback:
                stage_callback(
                    InspectionCycleState.INSPECTING,
                    "Inspecting terminal faces, finishes, markings, and rings",
                )
            for terminal in recipe.terminals:
                if not terminal.enabled:
                    continue
                terminal_crop = crop_normalized(location.aligned_battery, terminal.search_roi)
                marking_crop, effective_marking_roi = crop_marking_region(
                    terminal_crop,
                    terminal.marking_roi,
                    terminal.marking_roi_shape,
                )
                reference_terminal = crop_normalized(
                    location.reference_battery,
                    terminal.search_roi,
                )
                reference_marking, _reference_effective_marking_roi = crop_marking_region(
                    reference_terminal,
                    terminal.marking_roi,
                    terminal.marking_roi_shape,
                )
                face_validation = self.terminal_face_validator.validate(
                    marking_crop,
                    reference_marking,
                    recipe.classifier_settings,
                )
                if face_validation.present:
                    finish_validation = self.terminal_finish_validator.validate(
                        marking_crop,
                        reference_marking,
                        terminal.expected_finish,
                    )
                else:
                    finish_validation = TerminalFinishValidation(
                        detected=TerminalFinish.UNSPECIFIED,
                        confidence=0.0,
                        evaluated=False,
                        status="TERMINAL_FINISH_NOT_EVALUATED",
                        note="Terminal finish was not evaluated because the physical face is invalid.",
                        metrics={
                            "terminal_finish_validator": self.terminal_finish_validator.status,
                            "terminal_finish_expected": terminal.expected_finish.value,
                        },
                    )
                if face_validation.present and not classifier_readiness_issues:
                    classification = self.marking_classifier.classify(
                        marking_crop,
                        terminal,
                        recipe,
                        location.reference_battery,
                    )
                    classification.metrics.update(face_validation.metrics)
                    # Keep physical-input diagnostics in memory. They become
                    # retained evidence only when the cycle is non-PASS or is a
                    # guided validation capture.
                    for name, image in face_validation.diagnostic_images.items():
                        classification.diagnostic_images.setdefault(name, image)
                elif face_validation.present:
                    # Validation capture remains available even if the classifier
                    # is not commissioned/bound yet. Save the exact crops and
                    # physical-face diagnostics, but never fabricate a class or
                    # count the sample as a PASS.
                    classification = MarkingClassification(
                        marking=Marking.UNREADABLE,
                        confidence=0.0,
                        evaluated=False,
                        class_scores={},
                        metrics=dict(face_validation.metrics),
                        status="CLASSIFIER_NOT_READY",
                        note="; ".join(classifier_readiness_issues),
                        diagnostic_images=dict(face_validation.diagnostic_images),
                    )
                else:
                    # Do not ask marking ML to interpret an open hole,
                    # missing cap, or otherwise invalid physical terminal face.
                    # The classifier is intentionally bypassed and the product
                    # will be rejected by the physical gate below.
                    classification = MarkingClassification(
                        marking=Marking.UNREADABLE,
                        confidence=0.0,
                        evaluated=False,
                        class_scores={},
                        metrics=dict(face_validation.metrics),
                        status=face_validation.status,
                        note=face_validation.note,
                        diagnostic_images=dict(face_validation.diagnostic_images),
                    )
                for name, image in finish_validation.diagnostic_images.items():
                    classification.diagnostic_images.setdefault(name, image)
                red_present, red_confidence = self.ring_detector.detect(terminal_crop)
                terminal_polygon = location.map_battery_rect(terminal.search_roi)
                marking_battery_rect = rect_within(
                    terminal.search_roi,
                    effective_marking_roi,
                )
                if normalize_roi_shape(terminal.marking_roi_shape) == CIRCLE_ROI_SHAPE:
                    marking_polygon = location.map_battery_points(
                        circle_points(marking_battery_rect)
                    )
                else:
                    marking_polygon = location.map_battery_rect(marking_battery_rect)
                classification.metrics.setdefault(
                    "marking_roi_shape",
                    normalize_roi_shape(terminal.marking_roi_shape),
                )
                terminal_results.append(
                    TerminalInspection(
                        terminal_key=terminal.key,
                        terminal_name=terminal.name,
                        role=terminal.role,
                        expected_marking=terminal.expected_marking,
                        detected_marking=classification.marking,
                        marking_confidence=classification.confidence,
                        red_ring_expected=terminal.red_ring_required,
                        red_ring_detected=red_present,
                        red_ring_confidence=red_confidence,
                        expected_finish=terminal.expected_finish,
                        detected_finish=finish_validation.detected,
                        finish_confidence=finish_validation.confidence,
                        finish_evaluated=finish_validation.evaluated,
                        finish_status=finish_validation.status,
                        finish_note=finish_validation.note,
                        finish_metrics=finish_validation.metrics,
                        terminal_crop_path=None,
                        marking_crop_path=None,
                        marking_evaluated=classification.evaluated,
                        ring_evaluated=True,
                        analysis_note="; ".join(
                            part
                            for part in (
                                finish_validation.note,
                                classification.note,
                            )
                            if part
                        ),
                        reference_marking_path=None,
                        reference_similarity=classification.reference_similarity,
                        class_scores=classification.class_scores,
                        classification_metrics=classification.metrics,
                        classification_status=classification.status,
                        diagnostic_image_paths={},
                        terminal_polygon=terminal_polygon,
                        marking_polygon=marking_polygon,
                        terminal_face_evaluated=True,
                        terminal_face_present=face_validation.present,
                        terminal_face_confidence=face_validation.confidence,
                        terminal_face_status=face_validation.status,
                        terminal_crop_image=terminal_crop,
                        marking_crop_image=marking_crop,
                        reference_marking_image=reference_marking,
                        diagnostic_images=dict(classification.diagnostic_images),
                    )
                )

        if configuration_issues:
            disposition = InspectionDisposition.NOT_READY
            reason = self._reason_for_readiness(configuration_issues)
        elif locator_error:
            # A configured locator that cannot find the battery has completed a
            # valid inspection decision: the presented product cannot be
            # accepted. Treat it as a product reject (PLC code 4), while keeping
            # the registration error in the evidence manifest for maintenance.
            disposition = InspectionDisposition.REJECT
            reason = "BATTERY COULD NOT BE LOCATED"
        elif not terminal_results:
            disposition = InspectionDisposition.NOT_READY
            reason = "NO ENABLED TERMINALS IN RECIPE"
        elif any(
            item.terminal_face_evaluated and not item.terminal_face_present
            for item in terminal_results
        ):
            disposition = InspectionDisposition.REJECT
            reason = (
                "TERMINAL FACE MISSING"
                if any(
                    item.terminal_face_status == "TERMINAL_FACE_MISSING"
                    for item in terminal_results
                )
                else "TERMINAL FACE INVALID"
            )
        elif any(
            item.expected_finish != TerminalFinish.UNSPECIFIED
            and not item.finish_evaluated
            for item in terminal_results
        ):
            disposition = InspectionDisposition.REJECT
            reason = "TERMINAL FINISH NO DECISION"
        elif any(not item.finish_pass for item in terminal_results):
            disposition = InspectionDisposition.REJECT
            reason = "TERMINAL FINISH MISMATCH"
        elif any(not item.marking_evaluated or not item.ring_evaluated for item in terminal_results):
            disposition = InspectionDisposition.NOT_READY
            reason = "TERMINAL RESULT NOT EVALUATED"
        elif any(item.detected_marking == Marking.UNREADABLE for item in terminal_results):
            disposition = InspectionDisposition.REJECT
            ml_no_decision = any(
                item.detected_marking == Marking.UNREADABLE
                and bool(item.classification_metrics.get("ml_model_id"))
                for item in terminal_results
            )
            reason = "MARKING NO DECISION" if ml_no_decision else "UNREADABLE MARKING"
        elif any(
            item.detected_marking == Marking.INVALID_MARKING
            for item in terminal_results
        ):
            disposition = InspectionDisposition.REJECT
            reason = "INVALID MARKING"
        elif all(item.passed for item in terminal_results):
            disposition = InspectionDisposition.PASS
            reason = "INSPECTION PASSED"
        else:
            disposition = InspectionDisposition.REJECT
            enabled_results = [item for item in terminal_results if item.marking_evaluated]
            reversed_pair = (
                len(enabled_results) == 2
                and enabled_results[0].detected_marking
                not in {Marking.OTHER, Marking.INVALID_MARKING, Marking.UNREADABLE}
                and enabled_results[1].detected_marking
                not in {Marking.OTHER, Marking.INVALID_MARKING, Marking.UNREADABLE}
                and enabled_results[0].expected_marking
                == enabled_results[1].detected_marking
                and enabled_results[1].expected_marking
                == enabled_results[0].detected_marking
            )
            if reversed_pair:
                reason = "POLARITY MARKINGS REVERSED"
            elif any(not item.marking_pass for item in terminal_results):
                reason = "TERMINAL MARKING MISMATCH"
            else:
                reason = "RED RING MISMATCH"

        analysis_ready = bool(
            not configuration_issues
            and (
                bool(locator_error)
                or (
                    terminal_results
                    and all(
                        (
                            item.terminal_face_evaluated
                            and not item.terminal_face_present
                        )
                        or (
                            item.terminal_face_evaluated
                            and item.terminal_face_present
                            and (
                                item.expected_finish == TerminalFinish.UNSPECIFIED
                                or item.finish_evaluated
                            )
                            and item.marking_evaluated
                            and item.ring_evaluated
                        )
                        for item in terminal_results
                    )
                )
            )
        )
        result = InspectionResult.create(
            recipe=recipe,
            disposition=disposition,
            reason=reason,
            duration_ms=max(1, int((perf_counter() - started) * 1000)),
            trigger_source=trigger_source,
            image_quality=str(quality.get("status", "UNKNOWN")),
            full_image_path="",
            terminals=terminal_results,
            cycle_id=cycle_id,
            capture_id=capture_id,
            frame_id=captured.frame_id,
            frame_sequence=captured.sequence,
            captured_at_utc=captured.captured_at_utc,
            camera_frame_id=captured.camera_frame_id,
            camera_timestamp_raw=captured.camera_timestamp_raw,
            frame_width=captured.width,
            frame_height=captured.height,
            frame_channels=captured.channels,
            camera_backend=captured.backend_name,
            camera_description=(captured.device.display_name if captured.device else ""),
            evidence_directory="",
            analysis_ready=analysis_ready,
            readiness_issues=configuration_issues,
            locator_status=self.battery_locator.status,
            classifier_status=self.classifier_status_for_recipe(recipe),
            aligned_battery_path="",
            reference_battery_path="",
            battery_polygon=battery_polygon,
            locator_metrics=locator_metrics,
            full_image=captured.image,
            aligned_battery_image=(location.aligned_battery if location is not None else None),
            reference_battery_image=(
                location.reference_battery if location is not None else None
            ),
        )
        result.battery_roi = battery_roi
        retain_evidence = validation_mode or not result.passed
        if stage_callback:
            stage_callback(
                InspectionCycleState.SAVING,
                (
                    "Saving validation/failure evidence"
                    if retain_evidence
                    else "Finalizing PASS in memory — no evidence retained"
                ),
            )
        if retain_evidence:
            self._persist_inspection_result(
                result=result,
                captured=captured,
                recipe=recipe,
                quality=quality,
                locator_error=locator_error,
                validation_mode=validation_mode,
            )
            if not validation_mode:
                self.apply_failure_retention()
            # Retained results render from their just-written evidence paths;
            # avoid holding a second full image package in RAM.
            result.full_image = None
            for terminal in result.terminals:
                terminal.terminal_crop_image = None
                terminal.marking_crop_image = None
                terminal.diagnostic_images.clear()
        # These arrays are needed only for analysis/evidence materialization and
        # are not rendered by the current HMI. Release them before handing the
        # result to Qt so one displayed cycle has a bounded memory footprint.
        result.aligned_battery_image = None
        result.reference_battery_image = None
        for terminal in result.terminals:
            terminal.reference_marking_image = None
        return result

    @staticmethod
    def _safe_diagnostic_name(value: str) -> str:
        return "".join(
            character if character.isalnum() or character in {"_", "-"} else "_"
            for character in str(value)
        ).strip("_")

    def _persist_inspection_result(
        self,
        *,
        result: InspectionResult,
        captured: CameraFrame,
        recipe: Recipe | None,
        quality: dict[str, Any],
        locator_error: str,
        validation_mode: bool,
    ) -> None:
        """Materialize one validation or production non-PASS result."""

        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        category = "validation" if validation_mode else "inspections"
        directory = self.output_directory / category / day / result.cycle_id
        directory.mkdir(parents=True, exist_ok=False)
        result.evidence_directory = str(directory)
        result.full_image_path = save_jpeg(
            directory / "full.jpg",
            captured.image,
            quality=96,
        )
        write_json_atomic(directory / "capture.json", captured.metadata())

        if isinstance(result.aligned_battery_image, np.ndarray):
            result.aligned_battery_path = save_jpeg(
                directory / "aligned_battery.jpg",
                result.aligned_battery_image,
                quality=95,
            )
        if isinstance(result.reference_battery_image, np.ndarray):
            result.reference_battery_path = save_jpeg(
                directory / "reference_battery.jpg",
                result.reference_battery_image,
                quality=95,
            )

        for terminal in result.terminals:
            if isinstance(terminal.terminal_crop_image, np.ndarray):
                terminal.terminal_crop_path = save_png(
                    directory / f"{terminal.terminal_key}_terminal.png",
                    terminal.terminal_crop_image,
                )
            if isinstance(terminal.marking_crop_image, np.ndarray):
                terminal.marking_crop_path = save_png(
                    directory / f"{terminal.terminal_key}_marking.png",
                    terminal.marking_crop_image,
                )
            if isinstance(terminal.reference_marking_image, np.ndarray):
                terminal.reference_marking_path = save_png(
                    directory / f"{terminal.terminal_key}_reference_marking.png",
                    terminal.reference_marking_image,
                )
            paths: dict[str, str] = {}
            for name, image in sorted(terminal.diagnostic_images.items()):
                if not isinstance(image, np.ndarray) or image.size == 0:
                    continue
                safe_name = self._safe_diagnostic_name(str(name))
                if not safe_name:
                    continue
                paths[safe_name] = save_png(
                    directory / f"{terminal.terminal_key}_{safe_name}.png",
                    image,
                )
            terminal.diagnostic_image_paths = paths

        manifest_path = directory / "manifest.json"
        result.manifest_path = str(manifest_path)
        write_json_atomic(
            manifest_path,
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "software": software_build_info(),
                "validation_mode": validation_mode,
                "storage_policy": (
                    "validation"
                    if validation_mode
                    else "production_non_pass_only"
                ),
                "cycle": {
                    "cycle_id": result.cycle_id,
                    "trigger_source": result.trigger_source,
                },
                "capture": captured.metadata(),
                "quality": quality,
                "registration_error": locator_error,
                "recipe": (
                    {
                        "recipe_id": recipe.recipe_id,
                        "name": recipe.name,
                        "revision": recipe.revision,
                        "validation": {
                            "passed": recipe.validation_runs_passed,
                            "required": recipe.validation_runs_required,
                            "configuration_hash": recipe.validation_configuration_hash,
                        },
                        "orientation_reference": recipe.orientation_reference,
                        "locator_settings": recipe.locator_settings.to_dict(),
                        "classifier_settings": recipe.classifier_settings.to_dict(),
                        "engine": {
                            "locator_status": self.battery_locator.status,
                            "classifier_status": self.classifier_status_for_recipe(recipe),
                        },
                        "reference_image": (
                            recipe.reference_image.to_dict()
                            if recipe.reference_image
                            else None
                        ),
                    }
                    if recipe is not None
                    else None
                ),
                "result": result.to_dict(),
            },
        )

    def fault_result(
        self,
        *,
        recipe: Recipe | None,
        cycle_id: str,
        trigger_source: str,
        reason: str,
        details: str = "",
        frame: CameraFrame | None = None,
        duration_ms: int = 1,
    ) -> InspectionResult:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        directory = self.output_directory / "inspections" / day / cycle_id
        directory.mkdir(parents=True, exist_ok=True)
        full_path = ""
        quality = "UNKNOWN"
        if frame is not None and frame.image.size:
            full_path = save_jpeg(directory / "full.jpg", frame.image, quality=96)
            quality = str(assess_image_quality(frame.image).get("status", "UNKNOWN"))
        result = InspectionResult.create(
            recipe=recipe,
            disposition=InspectionDisposition.SYSTEM_FAULT,
            reason=reason,
            duration_ms=max(1, duration_ms),
            trigger_source=trigger_source,
            image_quality=quality,
            full_image_path=full_path,
            terminals=[],
            cycle_id=cycle_id,
            capture_id=str(uuid4()),
            frame_id=frame.frame_id if frame else "",
            frame_sequence=frame.sequence if frame else 0,
            captured_at_utc=frame.captured_at_utc if frame else "",
            camera_frame_id=frame.camera_frame_id if frame else "",
            camera_timestamp_raw=frame.camera_timestamp_raw if frame else None,
            frame_width=frame.width if frame else 0,
            frame_height=frame.height if frame else 0,
            frame_channels=frame.channels if frame else 0,
            camera_backend=frame.backend_name if frame else "",
            camera_description=(frame.device.display_name if frame and frame.device else ""),
            evidence_directory=str(directory),
            analysis_ready=False,
            readiness_issues=["SYSTEM_FAULT"],
            locator_status=self.battery_locator.status,
            classifier_status=self.classifier_status_for_recipe(recipe),
            full_image=(frame.image if frame is not None else None),
        )
        manifest = directory / "manifest.json"
        result.manifest_path = str(manifest)
        write_json_atomic(
            manifest,
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "software": software_build_info(),
                "validation_mode": False,
                "storage_policy": "production_non_pass_only",
                "cycle": {"cycle_id": cycle_id, "trigger_source": trigger_source},
                "capture": frame.metadata() if frame else None,
                "fault_details": details,
                "result": result.to_dict(),
            },
        )
        self.apply_failure_retention()
        return result
