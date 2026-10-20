# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Cosmetic plot strokes sized in logical pixels rather than physical pixels."""
import pyqtgraph as pg


def scale_stroke(item, logical_width, ratio):
    """Apply a logical width at the paint device ratio, preserving brush and style."""
    # Cosmetic pens stay independent of data-axis zoom while following display scaling.
    pen = item.opts["pen"] if isinstance(item, pg.PlotDataItem) else item.pen
    width = logical_width * ratio
    if pen.widthF() != width:
        pen = pg.mkPen(pen)
        pen.setWidthF(width)
        item.setPen(pen)


def register_stroke(item, logical_width, plot):
    """Mark a curve or guide for rescaling whenever its plot is painted."""
    item.setProperty("logicalStrokeWidth", logical_width)
    scale_stroke(item, logical_width, plot.devicePixelRatioF())
