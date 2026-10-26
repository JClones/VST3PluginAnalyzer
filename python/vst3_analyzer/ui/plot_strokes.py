# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Cosmetic plot strokes sized in logical pixels rather than physical pixels."""
import numpy as np
import pyqtgraph as pg

from ..measurements.modes import SPECTRUM_MODES


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


def set_curve_rendering(curve, values, measurement):
    """Keep smooth response joins, but avoid Qt's costly stroker on jagged spectra."""
    segmented = measurement in SPECTRUM_MODES
    if measurement == "frequency" and len(values) >= 256:
        # Dense reverberant responses can have thousands of tight turns. Stroking
        # their joined path can block the GUI for a second, even with a small FFT.
        # Comparisons leave NaN gaps intact and avoid arithmetic on extreme values.
        rising = values[1:] > values[:-1]
        falling = values[1:] < values[:-1]
        turns = np.count_nonzero((rising[1:] & falling[:-1]) | (falling[1:] & rising[:-1]))
        segmented = turns >= 128
    curve.curve.setSegmentedLineMode("on" if segmented else "off")
