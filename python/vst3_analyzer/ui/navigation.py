# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Directional rectangle zoom gestures independent of audio processing and FFT data."""
import math
from PySide6.QtCore import QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QGuiApplication, QInputDevice
import pyqtgraph as pg
from .plot_strokes import scale_stroke


class AnalyzerPlotWidget(pg.PlotWidget):
    """Keep high-resolution touchpad scrolling separate from traditional wheel zoom."""

    pointer_left = Signal()  # Clear hover overlays when the pointer leaves the viewport.

    def paintEvent(self, event):
        """Refresh cosmetic widths before rendering, including after a monitor change."""
        ratio = self.devicePixelRatioF()
        # PlotItem retains only current items; removed snapshots require no cleanup registry.
        for item in self.plotItem.items:
            width = item.property("logicalStrokeWidth")
            if width is not None:
                scale_stroke(item, width, ratio)
        for axis in self.plotItem.axes.values():
            item = axis["item"]
            pen = item.pen()
            if pen.widthF() != 0.5 * ratio:
                pen = pg.mkPen(pen)
                pen.setWidthF(0.5 * ratio)
                item.setPen(pen)
        if getattr(self, "_border_ratio", None) != ratio:
            self._border_ratio = ratio  # Avoid repeated graphics-bound invalidation.
            self.getViewBox().setBorder(pg.mkPen("#000000", width=0.5 * ratio))
        super().paintEvent(event)

    def viewportEvent(self, event):
        """Apply Qt pinch magnification around the gesture position without platform APIs."""
        if event.type() == QEvent.Leave:
            self.pointer_left.emit()
        if event.type() != QEvent.NativeGesture:
            return super().viewportEvent(event)
        box = self.getViewBox()
        scene = self.mapToScene(event.position().toPoint())
        if not box.sceneBoundingRect().contains(scene):
            return super().viewportEvent(event)
        kind = event.gestureType()
        if kind == Qt.ZoomNativeGesture:
            # Qt supplies incremental item magnification; visible data spans scale by its inverse.
            magnification = 1.0 + event.value()
            if math.isfinite(magnification) and magnification > 0:
                factor = 1.0 / magnification
                # Native pinch events may omit modifiers, and Qt's key-event cache can be stale.
                # Query held keys directly on every update, including modifier changes mid-gesture.
                box.zoom_touchpad(factor, box.mapSceneToView(scene),
                                  QGuiApplication.queryKeyboardModifiers())
            event.accept()
            return True
        if kind in (Qt.BeginNativeGesture, Qt.EndNativeGesture, Qt.RotateNativeGesture):
            # Pinch streams can include rotation; consume it without rotating or shifting the plot.
            event.accept()
            return True
        return super().viewportEvent(event)

    def wheelEvent(self, event):
        """Pan with two-finger scroll deltas before Qt discards their high-resolution metadata."""
        # GraphicsScene wheel events omit pixelDelta and gesture phase, so inspect the original event here.
        pixels = event.pixelDelta()
        angles = event.angleDelta()
        touchpad = (not pixels.isNull() or event.phase() != Qt.NoScrollPhase or
                    event.device().type() == QInputDevice.DeviceType.TouchPad or angles.x() != 0)
        zoom_modifier = bool(event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier))
        if not touchpad or (zoom_modifier and not angles.isNull()):
            return super().wheelEvent(event)
        box = self.getViewBox()
        position = event.position()
        scene = self.mapToScene(position.toPoint())
        if not box.sceneBoundingRect().contains(scene):
            return super().wheelEvent(event)

        enabled = box.state["mouseEnabled"]
        if zoom_modifier:
            # Pixel-only touchpad events have no GraphicsScene wheel delta; zoom them directly.
            distance = (pixels.y() or pixels.x()) * 3.0
            factor = 1.02 ** (distance * box.state["wheelScaleFactor"])
            box.zoom_touchpad(factor, box.mapSceneToView(scene), event.modifiers())
            event.accept()
            return

        # Respect Qt's OS-provided scroll direction; never invert natural scrolling a second time.
        delta = QPointF(pixels) if not pixels.isNull() else QPointF(angles) * (40.0 / 120.0)
        before = box.mapSceneToView(scene)
        after = box.mapSceneToView(self.mapToScene((position + delta).toPoint()))
        box._resetTarget()
        box.translateBy(x=before.x() - after.x() if enabled[0] else None,
                        y=before.y() - after.y() if enabled[1] else None)
        box.sigRangeChangedManually.emit(enabled)
        event.accept()


class AnalyzerViewBox(pg.ViewBox):
    """Zoom down-right; reset both, Y or X for the other diagonal drag directions."""

    reset_requested = Signal(bool, bool)  # Whether to reset the X and Y viewport ranges respectively.

    def __init__(self, **kwargs):
        """Start with directional box zoom on the left button and panning on the right."""
        super().__init__(**kwargs)
        # No tool selection is required; mouse buttons choose their gestures directly.
        self.default_x_min = math.log10(10)  # Log-frequency reset edge supplied by the spectrum configuration.
        self.setMouseMode(self.RectMode)
        self.setCursor(Qt.CrossCursor)

    def zoom_touchpad(self, factor, center, modifiers=Qt.NoModifier):
        """Scale around the gesture, capping horizontal zoom-out at the configured default frequency interval.

        Args:
            factor: View-span multiplier, greater than one for zoom-out.
            center: Gesture anchor in logarithmic X and linear Y plot coordinates.
            modifiers: Shift selects Y only; Alt/Option selects X only; neither or both selects both axes.
        """
        enabled = list(self.state["mouseEnabled"])
        # Modifier constraints are local to this gesture; do not change persistent mouse-axis settings.
        shift = bool(modifiers & Qt.ShiftModifier)
        alt = bool(modifiers & Qt.AltModifier)
        if shift != alt:
            enabled[0] = enabled[0] and alt
            enabled[1] = enabled[1] and shift
        lower = self.default_x_min
        upper = self.state["limits"]["xLimits"][1]
        bounded = factor > 1 and enabled[0] and upper is not None and upper > lower
        before = self.viewRange()[0][:]
        # Continue scaling Y even after X has reached its complete useful frequency range.
        self._resetTarget()
        self.scaleBy((factor if enabled[0] and not bounded else None,
                      factor if enabled[1] else None), center=center)
        if bounded:
            # Clamp the proposed horizontal interval, preserving the anchor until a boundary is reached.
            span = min((before[1] - before[0]) * factor, upper - lower)
            left = max(lower, min(center.x() + (before[0] - center.x()) * factor, upper - span))
            self.setXRange(left, left + span, padding=0)
        self.sigRangeChangedManually.emit(enabled)

    def mouseDragEvent(self, event, axis=None):
        """Interpret left-button rectangle gestures using screen-space drag directions."""
        # Right-button drags translate in plot coordinates instead of the library's scaling gesture.
        if event.button() == Qt.RightButton:
            event.accept()
            self.setCursor(Qt.CrossCursor if event.isFinish() else Qt.ClosedHandCursor)
            delta = self.mapToView(event.lastPos()) - self.mapToView(event.pos())
            enabled = self.state["mouseEnabled"]
            self._resetTarget()
            self.translateBy(x=delta.x() if enabled[0] and axis != 1 else None,
                             y=delta.y() if enabled[1] and axis != 0 else None)
            self.sigRangeChangedManually.emit(enabled)
            return
        # Leave middle-button and axis-only gestures to the plotting library.
        if (self.state["mouseMode"] != self.RectMode or axis is not None or
                event.button() != Qt.LeftButton):
            return super().mouseDragEvent(event, axis=axis)
        start = event.buttonDownPos(Qt.LeftButton)
        delta = event.pos() - start
        right, down = delta.x() > 0, delta.y() > 0
        if right and down and abs(delta.x()) >= 3 and abs(delta.y()) >= 3:
            # Cosmetic grid-width dashes stay the same size as the rectangle is transformed.
            ratio = self.scene().views()[0].devicePixelRatioF()
            pen = pg.mkPen((0, 0, 0, int(255 * 0.45)), width=0.5 * ratio, style=Qt.DashLine)
            pen.setCosmetic(True)
            pen.setDashPattern([8, 5])  # Long strokes remain visibly dashed at the grid line width.
            self.rbScaleBox.setPen(pen)
            self.rbScaleBox.setBrush(pg.mkBrush(None))
            return super().mouseDragEvent(event, axis=axis)

        # Reset gestures never draw a rectangle; nearly horizontal/vertical drags do nothing.
        event.accept()
        self.rbScaleBox.hide()
        if event.isFinish() and abs(delta.x()) >= 3 and abs(delta.y()) >= 3:
            self.reset_requested.emit(not right, not down)
