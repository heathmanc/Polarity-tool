from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from battery_inspector.models import NormalizedRect
from battery_inspector.roi_geometry import (
    CIRCLE_ROI_SHAPE,
    circle_rect_from_drag,
    coerce_circle_rect,
    normalize_roi_shape,
)
from battery_inspector.ui.palette import (
    SURFACE_STRONG,
    TEXT,
    VIEWPORT_BACKGROUND,
    VIEWPORT_BORDER,
    VIEWPORT_PLACEHOLDER,
)


def _contrast_text_color(background: QColor) -> QColor:
    """Return black or white text for an overlay label background."""

    luminance = (
        0.2126 * background.redF()
        + 0.7152 * background.greenF()
        + 0.0722 * background.blueF()
    )
    return QColor(TEXT) if luminance > 0.56 else QColor(SURFACE_STRONG)


@dataclass(slots=True)
class OverlaySpec:
    key: str
    rect: NormalizedRect
    label: str
    color: str
    dashed: bool = False
    line_width: int = 3
    shape: str = "rectangle"


@dataclass(slots=True)
class PolygonOverlaySpec:
    key: str
    points: list[tuple[float, float]]
    label: str
    color: str
    dashed: bool = False
    line_width: int = 3


def _load_pixmap(path: str | Path | None) -> QPixmap:
    if not path:
        return QPixmap()
    pixmap = QPixmap(str(path))
    return pixmap


def bgr_array_to_qimage(frame: np.ndarray) -> QImage:
    """Convert a NumPy camera frame to an owned QImage for safe UI display."""

    image = np.ascontiguousarray(frame)
    if image.ndim == 2:
        height, width = image.shape
        return QImage(
            image.data,
            width,
            height,
            image.strides[0],
            QImage.Format.Format_Grayscale8,
        ).copy()
    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise ValueError(f"Unsupported camera frame shape: {image.shape}")
    height, width, channels = image.shape
    image_format = (
        QImage.Format.Format_BGR888
        if channels == 3
        else QImage.Format.Format_RGBA8888
    )
    return QImage(
        image.data,
        width,
        height,
        image.strides[0],
        image_format,
    ).copy()


class CropPreview(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(180, 150)
        self.setStyleSheet(f"background: {VIEWPORT_BACKGROUND}; border: 1px solid {VIEWPORT_BORDER}; color: {VIEWPORT_PLACEHOLDER};")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._source = QPixmap()
        self.setText("NO IMAGE")

    def set_image(self, path: str | Path | None) -> None:
        self._source = _load_pixmap(path)
        if self._source.isNull():
            self.setText("NO IMAGE")
            self.setPixmap(QPixmap())
        else:
            self.setText("")
            self._refresh()

    def set_array(self, frame: np.ndarray | None) -> None:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            self._source = QPixmap()
            self.setText("NO IMAGE")
            self.setPixmap(QPixmap())
            return
        self._source = QPixmap.fromImage(bgr_array_to_qimage(frame))
        self.setText("")
        self._refresh()

    def set_pixmap_source(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._refresh()

    def _refresh(self) -> None:
        if self._source.isNull():
            return
        target = self.contentsRect().size()
        self.setPixmap(
            self._source.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh()


class ImageOverlayWidget(QWidget):
    image_clicked = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._overlays: list[OverlaySpec] = []
        self._polygon_overlays: list[PolygonOverlaySpec] = []
        self.setMinimumSize(420, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_image(self, path: str | Path | None) -> None:
        self._pixmap = _load_pixmap(path)
        self.update()

    def set_array(self, frame: np.ndarray | None) -> None:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            self._pixmap = QPixmap()
        else:
            self._pixmap = QPixmap.fromImage(bgr_array_to_qimage(frame))
        self.update()

    def set_qimage(self, image: QImage) -> None:
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    def set_pixmap_source(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def source_pixmap(self) -> QPixmap:
        return self._pixmap

    def set_overlays(self, overlays: list[OverlaySpec]) -> None:
        self._overlays = overlays
        self.update()

    def overlays(self) -> list[OverlaySpec]:
        return list(self._overlays)

    def set_polygon_overlays(self, overlays: list[PolygonOverlaySpec]) -> None:
        self._polygon_overlays = list(overlays)
        self.update()

    def polygon_overlays(self) -> list[PolygonOverlaySpec]:
        return list(self._polygon_overlays)

    def _image_target_rect(self) -> QRectF:
        if self._pixmap.isNull():
            return QRectF()
        available = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        source_ratio = self._pixmap.width() / max(1, self._pixmap.height())
        target_ratio = available.width() / max(1.0, available.height())
        if source_ratio > target_ratio:
            width = available.width()
            height = width / source_ratio
        else:
            height = available.height()
            width = height * source_ratio
        x = available.left() + (available.width() - width) / 2.0
        y = available.top() + (available.height() - height) / 2.0
        return QRectF(x, y, width, height)

    def normalized_to_widget(self, rect: NormalizedRect) -> QRectF:
        target = self._image_target_rect()
        return QRectF(
            target.left() + rect.x * target.width(),
            target.top() + rect.y * target.height(),
            rect.width * target.width(),
            rect.height * target.height(),
        )

    def widget_to_normalized_point(self, point: QPointF) -> QPointF | None:
        target = self._image_target_rect()
        if target.isEmpty() or not target.contains(point):
            return None
        x = (point.x() - target.left()) / target.width()
        y = (point.y() - target.top()) / target.height()
        return QPointF(min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0))

    def crop_pixmap(self, rect: NormalizedRect) -> QPixmap:
        if self._pixmap.isNull():
            return QPixmap()
        rect = rect.clamped()
        source_rect = QRectF(
            rect.x * self._pixmap.width(),
            rect.y * self._pixmap.height(),
            rect.width * self._pixmap.width(),
            rect.height * self._pixmap.height(),
        ).toAlignedRect()
        return self._pixmap.copy(source_rect)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(VIEWPORT_BACKGROUND))
        if self._pixmap.isNull():
            painter.setPen(QColor(VIEWPORT_PLACEHOLDER))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "NO IMAGE")
            return

        target = self._image_target_rect()
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        painter.setPen(QPen(QColor(VIEWPORT_BORDER), 1))
        painter.drawRect(target)

        for overlay in self._overlays:
            mapped = self.normalized_to_widget(overlay.rect)
            pen = QPen(QColor(overlay.color), overlay.line_width)
            if overlay.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if normalize_roi_shape(overlay.shape) == CIRCLE_ROI_SHAPE:
                painter.drawEllipse(mapped)
            else:
                painter.drawRect(mapped)

            label_metrics = painter.fontMetrics()
            label_width = label_metrics.horizontalAdvance(overlay.label) + 16
            label_height = label_metrics.height() + 8
            label_rect = QRectF(
                mapped.left(),
                max(target.top(), mapped.top() - label_height),
                label_width,
                label_height,
            )
            painter.fillRect(label_rect, QColor(overlay.color))
            painter.setPen(_contrast_text_color(QColor(overlay.color)))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, overlay.label)

        for overlay in self._polygon_overlays:
            points = [
                QPointF(
                    target.left() + x * target.width(),
                    target.top() + y * target.height(),
                )
                for x, y in overlay.points
            ]
            if len(points) < 3:
                continue
            polygon = QPolygonF(points)
            pen = QPen(QColor(overlay.color), overlay.line_width)
            if overlay.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(polygon)
            anchor = min(points, key=lambda point: point.y())
            metrics = painter.fontMetrics()
            label_width = metrics.horizontalAdvance(overlay.label) + 16
            label_height = metrics.height() + 8
            label_rect = QRectF(
                anchor.x(),
                max(target.top(), anchor.y() - label_height),
                label_width,
                label_height,
            )
            painter.fillRect(label_rect, QColor(overlay.color))
            painter.setPen(_contrast_text_color(QColor(overlay.color)))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, overlay.label)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        point = self.widget_to_normalized_point(event.position())
        if point is not None:
            self.image_clicked.emit(point.x(), point.y())
        super().mousePressEvent(event)


class RoiEditor(ImageOverlayWidget):
    roi_changed = Signal(str, object)
    selection_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editable: dict[str, OverlaySpec] = {}
        self._static_overlays: list[OverlaySpec] = []
        self._active_key: str | None = None
        self._drag_origin: QPointF | None = None
        self._starting_rect: NormalizedRect | None = None
        self._drag_mode: str | None = None
        self._minimum_extent = 0.015
        self._force_draw_next = False

    def set_static_overlays(self, overlays: list[OverlaySpec]) -> None:
        self._static_overlays = list(overlays)
        self._sync_overlays()

    def set_editable_rois(self, overlays: list[OverlaySpec]) -> None:
        normalized: list[OverlaySpec] = []
        for overlay in overlays:
            rect = overlay.rect.clamped()
            if (
                normalize_roi_shape(overlay.shape) == CIRCLE_ROI_SHAPE
                and not self._pixmap.isNull()
            ):
                rect = coerce_circle_rect(rect, self._pixmap.width(), self._pixmap.height())
            normalized.append(
                OverlaySpec(
                    key=overlay.key,
                    rect=rect,
                    label=overlay.label,
                    color=overlay.color,
                    dashed=overlay.dashed,
                    line_width=overlay.line_width,
                    shape=overlay.shape,
                )
            )
        self._editable = {overlay.key: overlay for overlay in normalized}
        self._sync_overlays()
        if normalized and self._active_key not in self._editable:
            self.set_active_key(normalized[0].key)

    def set_active_key(self, key: str) -> None:
        if key not in self._editable:
            return
        self._active_key = key
        self.selection_changed.emit(key)
        self._sync_overlays()

    def active_key(self) -> str | None:
        return self._active_key

    def begin_redraw(self, key: str | None = None) -> None:
        """Make the next press/drag redraw the active ROI instead of moving it."""

        if key is not None:
            self.set_active_key(key)
        if self._active_key is None:
            return
        self._force_draw_next = True
        self.setCursor(Qt.CursorShape.CrossCursor)

    def roi(self, key: str) -> NormalizedRect | None:
        overlay = self._editable.get(key)
        return overlay.rect if overlay else None

    def set_roi(self, key: str, rect: NormalizedRect) -> None:
        if key not in self._editable:
            return
        overlay = self._editable[key]
        candidate = rect.clamped()
        if (
            normalize_roi_shape(overlay.shape) == CIRCLE_ROI_SHAPE
            and not self._pixmap.isNull()
        ):
            candidate = coerce_circle_rect(
                candidate,
                self._pixmap.width(),
                self._pixmap.height(),
            )
        self._editable[key] = OverlaySpec(
            key=overlay.key,
            rect=candidate,
            label=overlay.label,
            color=overlay.color,
            dashed=overlay.dashed,
            line_width=overlay.line_width,
            shape=overlay.shape,
        )
        self._sync_overlays()
        self.roi_changed.emit(key, self._editable[key].rect)

    def nudge_size(self, key: str, scale: float) -> None:
        overlay = self._editable.get(key)
        if overlay is None:
            return
        rect = overlay.rect
        new_width = min(1.0, max(self._minimum_extent, rect.width * scale))
        new_height = min(1.0, max(self._minimum_extent, rect.height * scale))
        cx = rect.x + rect.width / 2.0
        cy = rect.y + rect.height / 2.0
        self.set_roi(
            key,
            NormalizedRect(cx - new_width / 2.0, cy - new_height / 2.0, new_width, new_height).clamped(),
        )

    def _sync_overlays(self) -> None:
        overlays: list[OverlaySpec] = list(self._static_overlays)
        for key, overlay in self._editable.items():
            width = max(overlay.line_width, 4 if key == self._active_key else overlay.line_width)
            overlays.append(
                OverlaySpec(
                    key=overlay.key,
                    rect=overlay.rect,
                    label=overlay.label,
                    color=overlay.color,
                    dashed=overlay.dashed,
                    line_width=width,
                    shape=overlay.shape,
                )
            )
        super().set_overlays(overlays)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        point = self.widget_to_normalized_point(event.position())
        if point is None:
            return

        if not self._force_draw_next:
            # Select an existing ROI when clicked.
            for key, overlay in reversed(list(self._editable.items())):
                rect = overlay.rect
                if rect.x <= point.x() <= rect.x + rect.width and rect.y <= point.y() <= rect.y + rect.height:
                    self.set_active_key(key)
                    break

        if self._active_key is None:
            return
        active_overlay = self._editable[self._active_key]
        current = active_overlay.rect
        inside = (
            current.x <= point.x() <= current.x + current.width
            and current.y <= point.y() <= current.y + current.height
        )
        self._drag_origin = point
        self._starting_rect = current
        self._drag_mode = "draw" if self._force_draw_next else ("move" if inside else "draw")
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._active_key is None or self._drag_origin is None or self._starting_rect is None:
            return
        point = self.widget_to_normalized_point(event.position())
        if point is None:
            return

        if self._drag_mode == "move":
            dx = point.x() - self._drag_origin.x()
            dy = point.y() - self._drag_origin.y()
            rect = NormalizedRect(
                self._starting_rect.x + dx,
                self._starting_rect.y + dy,
                self._starting_rect.width,
                self._starting_rect.height,
            ).clamped()
        else:
            overlay = self._editable[self._active_key]
            if normalize_roi_shape(overlay.shape) == CIRCLE_ROI_SHAPE:
                if self._pixmap.isNull():
                    return
                rect = circle_rect_from_drag(
                    self._drag_origin.x(),
                    self._drag_origin.y(),
                    point.x(),
                    point.y(),
                    self._pixmap.width(),
                    self._pixmap.height(),
                )
            else:
                x1 = min(self._drag_origin.x(), point.x())
                y1 = min(self._drag_origin.y(), point.y())
                x2 = max(self._drag_origin.x(), point.x())
                y2 = max(self._drag_origin.y(), point.y())
                rect = NormalizedRect(
                    x=x1,
                    y=y1,
                    width=max(self._minimum_extent, x2 - x1),
                    height=max(self._minimum_extent, y2 - y1),
                ).clamped()

        overlay = self._editable[self._active_key]
        if (
            normalize_roi_shape(overlay.shape) == CIRCLE_ROI_SHAPE
            and not self._pixmap.isNull()
        ):
            rect = coerce_circle_rect(
                rect,
                self._pixmap.width(),
                self._pixmap.height(),
            )
        self._editable[self._active_key] = OverlaySpec(
            key=overlay.key,
            rect=rect,
            label=overlay.label,
            color=overlay.color,
            dashed=overlay.dashed,
            line_width=overlay.line_width,
            shape=overlay.shape,
        )
        self._sync_overlays()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._active_key is not None and self._drag_origin is not None:
            rect = self._editable[self._active_key].rect
            self.roi_changed.emit(self._active_key, rect)
        self._drag_origin = None
        self._starting_rect = None
        self._drag_mode = None
        self._force_draw_next = False
        self.unsetCursor()
        event.accept()
