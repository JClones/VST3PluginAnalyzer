# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Level-dependent phase opacity, independent of measured phase and delay values."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QBrush, QLinearGradient


def phase_opacity(channel):
    """Fade from full at half the dB threshold to half opacity at the threshold.

    Reference-relative responses retain their original operand magnitudes; the
    ratio itself must not be used as a proxy for the strength of either source.
    """
    sources = channel.phase_sources or (channel.amplitudes_peak,)
    magnitude = np.minimum.reduce(sources)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 20 * np.log10(np.maximum(magnitude, 10**(channel.phase_threshold_db / 20)))
    opacity = np.clip(1.5 - db / channel.phase_threshold_db, 0.5, 1.0)
    return np.where(np.isfinite(opacity), opacity, 0.5)


def set_phase_opacity(curve, channel, view):
    """Attach original-bin opacity without changing curve geometry or numeric data."""
    curve.phase_opacity_data = ((np.log10(channel.frequencies_hz[1:]), phase_opacity(channel)[1:])
                                if view in ("wrapped", "unwrapped", "delay") else None)
    apply_opacity(curve, *curve.getViewBox().viewRange()[0])


def phase_linked(channels, linked, view):
    """Equal ratios may still need separate traces when their original levels differ."""
    if not linked or len(channels) < 2 or view not in ("wrapped", "unwrapped", "delay"):
        return linked
    return (np.array_equal(channels[0].phase_validity(), channels[1].phase_validity()) and
            np.array_equal(phase_opacity(channels[0]), phase_opacity(channels[1])))


def apply_opacity(curve, left, right, edge_fade=None):
    """Combine level opacity, existing pen alpha and optional stored-curve edge fade."""
    data = getattr(curve, "phase_opacity_data", None)
    if edge_fade is None:
        edge_fade = curve.property("storedColor") is not None
    base = pg.mkColor(curve.opts["pen"].color())
    pen = pg.mkPen(curve.opts["pen"])
    if not edge_fade and (data is None or np.all(data[1] >= 1)):
        pen.setBrush(QBrush(base))
        curve.setPen(pen)
        curve.opacity_gradient = None
        return
    span = right - left
    if span <= 0:
        return
    if data is None:
        knots = np.array([left, left + 0.05*span, right - 0.05*span, right])
        opacity = np.ones(knots.size)
    else:
        x, weights = data
        inside = x[(x > left) & (x < right)]
        # Two stops per logical pixel resolve visible changes without generating
        # tens of thousands of Qt gradient stops for high-resolution FFTs.
        limit = max(64, min(4096, int(curve.getViewBox().width() * 2)))
        if inside.size > limit:
            inside = np.linspace(left, right, limit)
        knots = np.unique(np.r_[left, inside, left+0.05*span, right-0.05*span, right])
        opacity = np.interp(knots, x, weights)
    if edge_fade:
        opacity *= np.interp(knots, [left, left+0.05*span, right-0.05*span, right], [0, 1, 1, 0])
    stops = []
    for x, weight in zip(knots, opacity):
        color = pg.mkColor(base)
        color.setAlphaF(base.alphaF() * float(weight))
        stops.append((float((x-left)/span), color))
    gradient = QLinearGradient(left, 0, right, 0)
    gradient.setStops(stops)
    brush = QBrush(gradient)
    brush.setColor(base)  # Preserve the unfaded base color/alpha for future refreshes.
    pen.setBrush(brush)
    curve.setPen(pen)
    curve.opacity_gradient = (left, right, stops)
