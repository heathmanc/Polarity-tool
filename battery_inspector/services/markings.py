from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from battery_inspector.models import Marking


@dataclass(slots=True)
class MarkingClassification:
    marking: Marking
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    diagnostic_images: dict[str, np.ndarray] = field(default_factory=dict)


@dataclass(slots=True)
class TerminalTopNormalization:
    """A terminal-top region normalized out of a larger taught marking crop.

    Threaded terminal heads can rotate independently of the battery case.  The
    taught marking ROI therefore acts as a *search area*, not as an exact stamp
    template.  This record keeps both the central stamp-analysis crop and enough
    source geometry to render useful evidence overlays.
    """

    crop: np.ndarray
    center_x_px: float
    center_y_px: float
    radius_px: float
    detection_confidence: float
    method: str
    candidate_count: int
    crop_bounds_px: tuple[int, int, int, int]
    source_width_px: int
    source_height_px: int
    overlay: np.ndarray

    def metrics(self) -> dict[str, Any]:
        extent = float(max(1, min(self.source_width_px, self.source_height_px)))
        center_offset_fraction = float(
            np.hypot(
                self.center_x_px - self.source_width_px / 2.0,
                self.center_y_px - self.source_height_px / 2.0,
            )
            / max(1.0, extent / 2.0)
        )
        inside_fraction = float(
            min(
                1.0,
                max(0.0, self.center_x_px / max(self.radius_px, 1.0)),
                max(
                    0.0,
                    (self.source_width_px - self.center_x_px)
                    / max(self.radius_px, 1.0),
                ),
                max(0.0, self.center_y_px / max(self.radius_px, 1.0)),
                max(
                    0.0,
                    (self.source_height_px - self.center_y_px)
                    / max(self.radius_px, 1.0),
                ),
            )
        )
        return {
            "terminal_top_center_px": [
                float(self.center_x_px),
                float(self.center_y_px),
            ],
            "terminal_top_center_normalized": [
                float(self.center_x_px / max(1, self.source_width_px)),
                float(self.center_y_px / max(1, self.source_height_px)),
            ],
            "terminal_top_radius_px": float(self.radius_px),
            "terminal_top_radius_fraction": float(
                self.radius_px / max(1, min(self.source_width_px, self.source_height_px))
            ),
            "terminal_top_detection_confidence": float(self.detection_confidence),
            "terminal_top_detection_method": self.method,
            "terminal_top_candidate_count": int(self.candidate_count),
            "terminal_top_center_offset_fraction": center_offset_fraction,
            "terminal_top_inside_fraction": inside_fraction,
            "stamp_analysis_bounds_px": [int(value) for value in self.crop_bounds_px],
        }


@dataclass(slots=True)
class GeometricStampThresholds:
    """Fail-closed thresholds for plus/minus/blank stamping geometry.

    The classifier does not use the recipe's expected answer.  It first finds
    the central circular terminal top, excluding outer hex flats, washers,
    knurling, and the red polarity ring.  It then measures one dominant groove
    versus two approximately perpendicular grooves.  The result is inherently
    rotation invariant, which is required because the threaded terminal head can
    rotate independently of the battery case.
    """

    analysis_size_px: int = 256
    minimum_contrast: float = 12.0
    minimum_sharpness: float = 5.0
    maximum_clipped_fraction: float = 0.55
    blank_maximum_signal: float = 0.045
    plus_minimum_signal: float = 0.060
    plus_minimum_orthogonal_ratio: float = 0.45
    minus_minimum_signal: float = 0.100
    minus_maximum_orthogonal_ratio: float = 0.25
    minimum_accepted_confidence: float = 0.70

    # Terminal-top search and central stamp crop.
    terminal_top_detection_max_dimension: int = 512
    terminal_top_minimum_radius_fraction: float = 0.16
    terminal_top_maximum_radius_fraction: float = 0.42
    terminal_top_target_radius_fraction: float = 0.29
    stamp_crop_half_extent_radius: float = 0.68

    # Geometry sanity gates.  They prevent an outer rim/hex edge from becoming
    # a false MINUS and require the two PLUS arms to intersect near the center.
    plus_maximum_intersection_offset_fraction: float = 0.18
    minus_maximum_center_offset_fraction: float = 0.22


class TerminalTopNormalizer:
    """Locate the central round terminal top inside a taught marking ROI."""

    def __init__(self, thresholds: GeometricStampThresholds) -> None:
        self.thresholds = thresholds

    @staticmethod
    def _gray(image: np.ndarray) -> np.ndarray:
        if image is None or image.size == 0:
            raise ValueError("Marking crop is empty")
        if image.ndim == 2:
            return image
        if image.ndim == 3 and image.shape[2] >= 3:
            return cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
        raise ValueError(f"Unsupported marking crop shape: {image.shape}")

    @staticmethod
    def _color(image: np.ndarray) -> np.ndarray:
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        return image[:, :, :3].copy()

    @staticmethod
    def _ring_edge_support(
        gradient: np.ndarray,
        center_x: float,
        center_y: float,
        radius: float,
    ) -> float:
        angles = np.linspace(0.0, 2.0 * np.pi, 240, endpoint=False)
        values: list[np.ndarray] = []
        height, width = gradient.shape[:2]
        for delta in (-2.0, 0.0, 2.0):
            current_radius = max(1.0, radius + delta)
            xs = np.rint(center_x + current_radius * np.cos(angles)).astype(np.int32)
            ys = np.rint(center_y + current_radius * np.sin(angles)).astype(np.int32)
            valid = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
            if np.any(valid):
                values.append(gradient[ys[valid], xs[valid]])
        if not values:
            return 0.0
        sampled = np.concatenate(values).astype(np.float32)
        scale = max(1.0, float(np.percentile(gradient, 96)))
        return float(np.clip(float(np.mean(sampled)) / scale, 0.0, 1.0))

    def _score_candidate(
        self,
        candidate: tuple[float, float, float],
        *,
        width: int,
        height: int,
        gradient: np.ndarray,
    ) -> float:
        x, y, radius = candidate
        extent = float(max(1, min(width, height)))
        center_distance = float(
            np.hypot(x - width / 2.0, y - height / 2.0) / max(1.0, extent / 2.0)
        )
        center_score = float(np.exp(-((center_distance / 0.48) ** 2)))
        radius_fraction = radius / extent
        radius_sigma = max(
            0.08,
            (
                self.thresholds.terminal_top_maximum_radius_fraction
                - self.thresholds.terminal_top_minimum_radius_fraction
            )
            / 2.0,
        )
        radius_score = float(
            np.exp(
                -(
                    (
                        radius_fraction
                        - self.thresholds.terminal_top_target_radius_fraction
                    )
                    / radius_sigma
                )
                ** 2
            )
        )
        edge_score = self._ring_edge_support(gradient, x, y, radius)
        inside_fraction = min(
            1.0,
            max(0.0, x / max(radius, 1.0)),
            max(0.0, (width - x) / max(radius, 1.0)),
            max(0.0, y / max(radius, 1.0)),
            max(0.0, (height - y) / max(radius, 1.0)),
        )
        return float(
            np.clip(
                0.52 * center_score
                + 0.23 * radius_score
                + 0.20 * edge_score
                + 0.05 * inside_fraction,
                0.0,
                1.0,
            )
        )

    def extract(self, image: np.ndarray) -> TerminalTopNormalization:
        gray = self._gray(image)
        color = self._color(image)
        height, width = gray.shape[:2]
        extent = max(1, min(height, width))

        detection_scale = min(
            1.0,
            float(max(128, self.thresholds.terminal_top_detection_max_dimension))
            / float(max(height, width)),
        )
        if detection_scale < 0.999:
            detection_gray = cv2.resize(
                gray,
                None,
                fx=detection_scale,
                fy=detection_scale,
                interpolation=cv2.INTER_AREA,
            )
        else:
            detection_gray = gray
        detection_extent = max(1, min(detection_gray.shape[:2]))
        sigma = max(1.2, detection_extent / 220.0)
        blurred = cv2.GaussianBlur(detection_gray, (0, 0), sigma)

        candidate_batches: list[np.ndarray] = []
        # Always evaluate both a conservative and a permissive Hough pass. A
        # strong outer washer/hex circle can be the only result from the first
        # pass even when the true central terminal top appears in the second.
        # Candidate scoring then selects the central, plausible-radius circle.
        for accumulator in (
            max(24, int(round(detection_extent * 0.090))),
            max(18, int(round(detection_extent * 0.070))),
        ):
            circles = cv2.HoughCircles(
                blurred,
                cv2.HOUGH_GRADIENT,
                dp=1.0,
                minDist=max(12.0, detection_extent * 0.12),
                param1=100.0,
                param2=float(accumulator),
                minRadius=max(
                    5,
                    int(
                        round(
                            detection_extent
                            * self.thresholds.terminal_top_minimum_radius_fraction
                        )
                    ),
                ),
                maxRadius=max(
                    7,
                    int(
                        round(
                            detection_extent
                            * self.thresholds.terminal_top_maximum_radius_fraction
                        )
                    ),
                ),
            )
            if circles is not None and len(circles[0]):
                candidate_batches.append(np.asarray(circles[0], dtype=np.float64))

        candidates_scaled: np.ndarray | None = None
        if candidate_batches:
            combined = np.concatenate(candidate_batches, axis=0)
            unique: list[np.ndarray] = []
            for candidate in combined:
                if any(
                    np.linalg.norm(candidate[:2] - existing[:2]) < 2.5
                    and abs(float(candidate[2] - existing[2])) < 2.5
                    for existing in unique
                ):
                    continue
                unique.append(candidate)
            candidates_scaled = np.asarray(unique, dtype=np.float64)

        gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        gradient = cv2.magnitude(gradient_x, gradient_y)

        candidates: list[tuple[float, float, float]] = []
        if candidates_scaled is not None:
            inverse_scale = 1.0 / max(detection_scale, 1e-9)
            candidates = [
                (
                    float(item[0] * inverse_scale),
                    float(item[1] * inverse_scale),
                    float(item[2] * inverse_scale),
                )
                for item in candidates_scaled
            ]

        if candidates:
            scored = [
                (
                    self._score_candidate(
                        candidate,
                        width=width,
                        height=height,
                        gradient=gradient,
                    ),
                    candidate,
                )
                for candidate in candidates
            ]
            confidence, selected = max(scored, key=lambda item: item[0])
            center_x, center_y, radius = selected
            method = "HOUGH_CIRCLE"
        else:
            center_x = width / 2.0
            center_y = height / 2.0
            radius = extent * self.thresholds.terminal_top_target_radius_fraction
            confidence = 0.20
            method = "CENTER_FALLBACK"

        half_extent = max(
            20,
            int(round(radius * self.thresholds.stamp_crop_half_extent_radius)),
        )
        center_x_i = int(round(center_x))
        center_y_i = int(round(center_y))
        source_bounds = (
            center_x_i - half_extent,
            center_y_i - half_extent,
            center_x_i + half_extent,
            center_y_i + half_extent,
        )
        pad = half_extent + 3
        padded = cv2.copyMakeBorder(
            color,
            pad,
            pad,
            pad,
            pad,
            cv2.BORDER_REFLECT_101,
        )
        padded_x = center_x_i + pad
        padded_y = center_y_i + pad
        crop = padded[
            padded_y - half_extent : padded_y + half_extent,
            padded_x - half_extent : padded_x + half_extent,
        ].copy()
        if crop.size == 0:
            raise ValueError("Could not extract the terminal-top analysis crop")

        overlay = color.copy()
        cv2.circle(
            overlay,
            (center_x_i, center_y_i),
            max(1, int(round(radius))),
            (0, 220, 255),
            max(2, round(extent / 180)),
            cv2.LINE_AA,
        )
        x1, y1, x2, y2 = source_bounds
        cv2.rectangle(
            overlay,
            (max(0, x1), max(0, y1)),
            (min(width - 1, x2), min(height - 1, y2)),
            (255, 180, 0),
            max(2, round(extent / 200)),
            cv2.LINE_AA,
        )

        return TerminalTopNormalization(
            crop=crop,
            center_x_px=float(center_x),
            center_y_px=float(center_y),
            radius_px=float(radius),
            detection_confidence=float(confidence),
            method=method,
            candidate_count=len(candidates),
            crop_bounds_px=source_bounds,
            source_width_px=width,
            source_height_px=height,
            overlay=overlay,
        )


class GeometricStampClassifier:
    """Rotation-invariant geometric classifier for stamped + / - / blank marks."""

    ready = True
    status = "GEOMETRIC_STAMP_V2"

    def __init__(
        self,
        thresholds: GeometricStampThresholds | None = None,
        *,
        terminal_top_normalizer: TerminalTopNormalizer | None = None,
    ) -> None:
        self.thresholds = thresholds or GeometricStampThresholds()
        self.terminal_top_normalizer = terminal_top_normalizer or TerminalTopNormalizer(
            self.thresholds
        )

    @staticmethod
    def _square_gray(image: np.ndarray, size: int) -> np.ndarray:
        if image is None or image.size == 0:
            raise ValueError("Marking crop is empty")
        if image.ndim == 2:
            gray = image
        elif image.ndim == 3 and image.shape[2] >= 3:
            gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
        else:
            raise ValueError(f"Unsupported marking crop shape: {image.shape}")
        height, width = gray.shape[:2]
        extent = min(height, width)
        y1 = max(0, (height - extent) // 2)
        x1 = max(0, (width - extent) // 2)
        gray = gray[y1 : y1 + extent, x1 : x1 + extent]
        interpolation = cv2.INTER_AREA if extent > size else cv2.INTER_CUBIC
        return cv2.resize(gray, (size, size), interpolation=interpolation)

    def _line_metrics(self, raw: np.ndarray) -> tuple[dict[str, float], np.ndarray]:
        size = raw.shape[0]
        enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(raw)
        background = cv2.GaussianBlur(
            enhanced,
            (0, 0),
            sigmaX=max(5.0, size / 27.0),
            sigmaY=max(5.0, size / 27.0),
        )
        local_deviation = cv2.absdiff(enhanced, background).astype(np.float32)
        percentile_50 = float(np.percentile(local_deviation, 50))
        percentile_90 = float(np.percentile(local_deviation, 90))
        normalized = np.clip(
            (local_deviation - percentile_50)
            / max(1.0, percentile_90 - percentile_50),
            0.0,
            2.0,
        )

        yy, xx = np.ogrid[:size, :size]
        radius = np.sqrt(
            (xx - (size - 1) / 2.0) ** 2 + (yy - (size - 1) / 2.0) ** 2
        )
        radial_weight = np.exp(-((radius / (size * 0.42)) ** 6)).astype(np.float32)
        analysis_mask = (radius < size * 0.46).astype(np.float32)
        response_image = normalized * radial_weight * analysis_mask

        center = (size / 2.0, size / 2.0)
        x1 = int(size * 0.18)
        x2 = int(size * 0.82)
        records: list[tuple[float, int, float]] = []
        for angle in range(0, 180, 2):
            rotation = cv2.getRotationMatrix2D(center, -float(angle), 1.0)
            rotated = cv2.warpAffine(
                response_image,
                rotation,
                (size, size),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
            )
            row_mean = rotated[:, x1:x2].mean(axis=1).astype(np.float32)
            narrow = cv2.blur(row_mean.reshape(-1, 1), (1, 7)).reshape(-1)
            broad = cv2.blur(row_mean.reshape(-1, 1), (1, 29)).reshape(-1)
            row_response = narrow - 0.42 * broad
            low = int(size * 0.18)
            high = max(low + 1, int(size * 0.82))
            row = low + int(np.argmax(row_response[low:high]))
            records.append(
                (float(row_response[row]), angle, float(row - size / 2.0))
            )

        response_values = np.asarray([record[0] for record in records], dtype=np.float32)
        baseline = float(np.percentile(response_values, 35))
        primary_response, primary_index, primary_offset = max(records)

        def angular_distance(first: int, second: int) -> float:
            return abs(((float(first - second) + 90.0) % 180.0) - 90.0)

        orthogonal_candidates = [
            record
            for record in records
            if angular_distance(record[1], (primary_index + 90) % 180) <= 12.0
        ]
        orthogonal_response, orthogonal_index, orthogonal_offset = max(
            orthogonal_candidates
        )
        primary_signal = max(0.0, primary_response - baseline)
        orthogonal_signal = max(0.0, orthogonal_response - baseline)
        orthogonal_ratio = orthogonal_signal / max(primary_signal, 1e-6)
        intersection_offset = float(
            np.hypot(primary_offset, orthogonal_offset)
        )
        stamp_angle = float(((float(primary_index) + 90.0) % 180.0) - 90.0)

        return (
            {
                "primary_angle_deg": float(primary_index),
                "stamp_angle_deg": stamp_angle,
                "orthogonal_angle_deg": float(orthogonal_index),
                "primary_offset_px": float(primary_offset),
                "orthogonal_offset_px": float(orthogonal_offset),
                "intersection_offset_px": intersection_offset,
                "intersection_offset_fraction": intersection_offset / float(size),
                "baseline_response": baseline,
                "primary_response": primary_response,
                "orthogonal_response": orthogonal_response,
                "line_signal": primary_signal,
                "primary_line_signal": primary_signal,
                "orthogonal_signal": orthogonal_signal,
                "orthogonal_line_signal": orthogonal_signal,
                "orthogonal_ratio": float(orthogonal_ratio),
            },
            response_image,
        )

    @staticmethod
    def _clamp01(value: float) -> float:
        return float(np.clip(value, 0.0, 1.0))

    @staticmethod
    def _response_preview(response: np.ndarray) -> np.ndarray:
        high = max(1e-6, float(np.percentile(response, 99)))
        visual = np.clip(response / high, 0.0, 1.0)
        mono = np.uint8(np.rint(visual * 255.0))
        return cv2.applyColorMap(mono, cv2.COLORMAP_TURBO)

    @staticmethod
    def _canonical_preview(image: np.ndarray, stamp_angle_deg: float) -> np.ndarray:
        height, width = image.shape[:2]
        matrix = cv2.getRotationMatrix2D(
            (width / 2.0, height / 2.0),
            float(stamp_angle_deg),
            1.0,
        )
        return cv2.warpAffine(
            image,
            matrix,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT_101,
        )

    @staticmethod
    def _draw_line(
        image: np.ndarray,
        center: tuple[int, int],
        radius: float,
        angle_deg: float,
        color: tuple[int, int, int],
        thickness: int,
    ) -> None:
        angle = np.deg2rad(-float(angle_deg))
        length = max(8.0, radius * 0.58)
        dx = float(np.cos(angle) * length)
        dy = float(np.sin(angle) * length)
        cv2.line(
            image,
            (int(round(center[0] - dx)), int(round(center[1] - dy))),
            (int(round(center[0] + dx)), int(round(center[1] + dy))),
            color,
            thickness,
            cv2.LINE_AA,
        )

    def classify(self, marking_crop: np.ndarray) -> MarkingClassification:
        size = max(128, int(self.thresholds.analysis_size_px))
        try:
            top = self.terminal_top_normalizer.extract(marking_crop)
            raw = self._square_gray(top.crop, size)
        except ValueError as exc:
            return MarkingClassification(
                marking=Marking.UNREADABLE,
                confidence=0.0,
                status="INVALID_CROP",
                metrics={"error": str(exc)},
            )

        percentile_5, percentile_95 = np.percentile(raw, [5, 95])
        contrast = float(percentile_95 - percentile_5)
        sharpness = float(np.var(cv2.Laplacian(raw, cv2.CV_64F)))
        clipped_fraction = float(np.mean((raw <= 5) | (raw >= 250)))
        mean_level = float(raw.mean())
        metrics: dict[str, Any] = {
            "contrast_p95_p5": contrast,
            "sharpness_laplacian_variance": sharpness,
            "clipped_fraction": clipped_fraction,
            "mean_level": mean_level,
            **top.metrics(),
        }
        diagnostic_images: dict[str, np.ndarray] = {
            "terminal_top": top.crop,
        }

        if (
            contrast < self.thresholds.minimum_contrast
            or sharpness < self.thresholds.minimum_sharpness
            or clipped_fraction > self.thresholds.maximum_clipped_fraction
        ):
            metrics.update(
                {
                    "quality_gate": "FAILED",
                    "minimum_contrast": self.thresholds.minimum_contrast,
                    "minimum_sharpness": self.thresholds.minimum_sharpness,
                    "maximum_clipped_fraction": self.thresholds.maximum_clipped_fraction,
                }
            )
            diagnostic_images["stamp_overlay"] = top.overlay
            return MarkingClassification(
                marking=Marking.UNREADABLE,
                confidence=0.0,
                scores={
                    Marking.PLUS.value: 0.0,
                    Marking.MINUS.value: 0.0,
                    Marking.BLANK.value: 0.0,
                    Marking.UNREADABLE.value: 1.0,
                },
                metrics=metrics,
                status="IMAGE_QUALITY_FAILED",
                diagnostic_images=diagnostic_images,
            )

        line, response = self._line_metrics(raw)
        metrics.update(line)
        signal = float(line["line_signal"])
        ratio = float(line["orthogonal_ratio"])
        primary_offset_fraction = abs(float(line["primary_offset_px"])) / float(size)
        intersection_offset_fraction = float(line["intersection_offset_fraction"])

        plus_signal_score = self._clamp01(
            (signal - self.thresholds.plus_minimum_signal) / 0.35
        )
        plus_ratio_score = self._clamp01(
            (ratio - self.thresholds.plus_minimum_orthogonal_ratio) / 0.35
        )
        plus_center_score = self._clamp01(
            1.0
            - intersection_offset_fraction
            / max(
                1e-6,
                self.thresholds.plus_maximum_intersection_offset_fraction,
            )
        )
        minus_signal_score = self._clamp01(
            (signal - self.thresholds.minus_minimum_signal) / 0.35
        )
        minus_ratio_score = self._clamp01(
            (self.thresholds.minus_maximum_orthogonal_ratio - ratio)
            / max(1e-6, self.thresholds.minus_maximum_orthogonal_ratio)
        )
        minus_center_score = self._clamp01(
            1.0
            - primary_offset_fraction
            / max(
                1e-6,
                self.thresholds.minus_maximum_center_offset_fraction,
            )
        )
        blank_signal_score = self._clamp01(
            (self.thresholds.blank_maximum_signal - signal)
            / max(1e-6, self.thresholds.blank_maximum_signal)
        )

        plus_gate = bool(
            signal >= self.thresholds.plus_minimum_signal
            and ratio >= self.thresholds.plus_minimum_orthogonal_ratio
            and intersection_offset_fraction
            <= self.thresholds.plus_maximum_intersection_offset_fraction
        )
        minus_gate = bool(
            signal >= self.thresholds.minus_minimum_signal
            and ratio <= self.thresholds.minus_maximum_orthogonal_ratio
            and primary_offset_fraction
            <= self.thresholds.minus_maximum_center_offset_fraction
        )
        blank_gate = bool(signal <= self.thresholds.blank_maximum_signal)

        if plus_gate:
            plus_score = self._clamp01(
                0.62
                + 0.18 * plus_ratio_score
                + 0.12 * plus_signal_score
                + 0.08 * plus_center_score
            )
        else:
            plus_score = self._clamp01(
                0.34 * plus_ratio_score
                + 0.10 * plus_signal_score
                + 0.05 * plus_center_score
            )
        if minus_gate:
            minus_score = self._clamp01(
                0.62
                + 0.18 * minus_ratio_score
                + 0.12 * minus_signal_score
                + 0.08 * minus_center_score
            )
        else:
            minus_score = self._clamp01(
                0.34 * minus_ratio_score
                + 0.10 * minus_signal_score
                + 0.05 * minus_center_score
            )
        if blank_gate:
            blank_score = self._clamp01(0.72 + 0.28 * blank_signal_score)
        else:
            blank_score = self._clamp01(0.20 * blank_signal_score)

        if plus_gate and plus_score >= minus_score:
            marking = Marking.PLUS
            confidence = plus_score
            status = "TWO_PERPENDICULAR_LINES"
        elif minus_gate:
            marking = Marking.MINUS
            confidence = minus_score
            status = "ONE_DOMINANT_LINE"
        elif blank_gate:
            marking = Marking.BLANK
            confidence = blank_score
            status = "NO_DOMINANT_STAMP_LINE"
        else:
            marking = Marking.UNREADABLE
            confidence = max(0.0, 1.0 - max(plus_score, minus_score, blank_score))
            status = "AMBIGUOUS_GEOMETRY"

        if (
            marking != Marking.UNREADABLE
            and confidence < self.thresholds.minimum_accepted_confidence
        ):
            marking = Marking.UNREADABLE
            status = "CONFIDENCE_BELOW_GATE"

        scores = {
            Marking.PLUS.value: float(plus_score),
            Marking.MINUS.value: float(minus_score),
            Marking.BLANK.value: float(blank_score),
            Marking.UNREADABLE.value: float(
                1.0
                if marking == Marking.UNREADABLE
                else max(0.0, 1.0 - confidence)
            ),
        }
        metrics.update(
            {
                "quality_gate": "PASSED",
                "classification_gate": self.thresholds.minimum_accepted_confidence,
                "plus_gate": plus_gate,
                "minus_gate": minus_gate,
                "blank_gate": blank_gate,
                "primary_offset_fraction": primary_offset_fraction,
                "plus_center_score": plus_center_score,
                "minus_center_score": minus_center_score,
            }
        )

        overlay = top.overlay.copy()
        center = (int(round(top.center_x_px)), int(round(top.center_y_px)))
        thickness = max(2, round(min(top.source_width_px, top.source_height_px) / 160))
        self._draw_line(
            overlay,
            center,
            top.radius_px,
            float(line["stamp_angle_deg"]),
            (60, 240, 80),
            thickness,
        )
        if float(line["orthogonal_ratio"]) >= 0.20:
            self._draw_line(
                overlay,
                center,
                top.radius_px,
                float(line["orthogonal_angle_deg"]),
                (255, 190, 40),
                thickness,
            )
        diagnostic_images.update(
            {
                "stamp_overlay": overlay,
                "stamp_response": self._response_preview(response),
                "canonical_stamp": self._canonical_preview(
                    top.crop,
                    float(line["stamp_angle_deg"]),
                ),
            }
        )
        return MarkingClassification(
            marking=marking,
            confidence=float(confidence),
            scores=scores,
            metrics=metrics,
            status=status,
            diagnostic_images=diagnostic_images,
        )
