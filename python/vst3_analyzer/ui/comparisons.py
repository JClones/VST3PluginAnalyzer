# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Session-only channel-tinted plot snapshots, independent of processor state and live FFT buffers."""
from copy import deepcopy
from dataclasses import dataclass
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QLinearGradient

from .phase_curve import wrapped_phase_paths
from .plot_strokes import register_stroke
from ..measurements.modes import SPECTRUM_MODES

TRACE_COLORS = ("#ff00ff", "#0000f5", "#ea3323")  # Linked/mono, left and right colors shared by live and stored traces.
STORED_CURVE_OPACITY = 0.5  # Half-transparent main strokes; wrap connectors retain their additional attenuation.


MS_COLORS = ("#d98200", "#009b55")  # Mid orange and Side green, distinct from the L/R palette.


@dataclass
class StoredPlot:
    """One Store operation, containing copies of all visible channel paths."""

    measurement: str  # Measurement whose axes give these paths their meaning.
    view: str  # Currently rendered response representation, or magnitude for harmonic spectra.
    curves: tuple  # Owned tinted PlotDataItems for the currently rendered view.
    responses: tuple = ()  # Frozen complex response per channel, including any reference applied at capture.
    linked: bool = True  # Whether the captured displayed stereo channels shared one trace.
    harmonic_channels: tuple = ()  # Frozen full-bin harmonic/generator spectra, including DC, for numeric export.
    ms_mode: bool = False  # Frozen channel interpretation for colors and numeric export.
    impulse_channels: tuple = ()  # Frozen raw impulse responses, including negative sample times.


def display_values(channel, view):
    """Return the selected view's float64 values at every original FFT bin.

    Args:
        channel: One harmonic spectrum or complex frequency response.
        view: Magnitude, wrapped/unwrapped phase, or sample-delay representation.
    """
    # Retain DC here for data export; logarithmic plotting excludes it at the presentation boundary.
    if view == "impulse":
        return channel.samples
    if view == "magnitude":
        return 20 * np.log10(np.maximum(channel.amplitudes_peak, np.finfo(np.float64).tiny))
    component = {"wrapped": 0, "unwrapped": 1, "delay": 2}[view]
    return channel.phase_and_delay()[component]


def fade_stored_curve(curve, left, right):
    """Fade a stored stroke over five percent of each visible horizontal edge.

    Args:
        curve: Stored PlotDataItem whose sample coordinates remain unchanged.
        left: Visible left boundary in logarithmic plot coordinates.
        right: Visible right boundary in logarithmic plot coordinates.
    """
    # A logical-coordinate brush follows the plot transform, so fades track screen width during zoom/pan.
    color = pg.mkColor(curve.property("storedColor"))
    color.setAlpha(curve.property("storedAlpha"))
    transparent = pg.mkColor(color)
    transparent.setAlpha(0)
    gradient = QLinearGradient(left, 0, right, 0)
    for position, tint in ((0, transparent), (0.05, color), (0.95, color), (1, transparent)):
        gradient.setColorAt(position, tint)
    brush = QBrush(gradient)
    brush.setColor(color)
    pen = pg.mkPen(curve.opts["pen"])
    pen.setBrush(brush)
    curve.setPen(pen)


def add_stored_curve(plot, x, y, pen, measurement, connect="finite"):
    """Create one channel-tinted path with common stroke styling and viewport-edge fades.

    Args:
        plot: PlotWidget owning the new graphics item.
        x: Positive frequency coordinates to copy.
        y: Displayed ordinate values to copy.
        pen: Source channel color, stroke style and opacity, rendered at reduced width.
        measurement: Measurement identifier controlling dense-spectrum drawing optimization.
        connect: PlotDataItem connection policy, copied when supplied as an array.
    """
    pen = pg.mkPen(pen)
    color = pen.color()
    # Mute linked magenta a little more than the stereo tints, preserving the established red/blue colors.
    tint_weight = 0.35 if color.name() == TRACE_COLORS[0] else 0.5
    color.setRgb(*(round(component * tint_weight + 153 * (1 - tint_weight)) for component in
                   (color.red(), color.green(), color.blue())), color.alpha())
    color.setAlpha(round(color.alpha() * STORED_CURVE_OPACITY))
    pen.setColor(color)
    pen.setWidthF(1.25)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    if isinstance(connect, np.ndarray):
        connect = connect.copy()
    # Keep graphics buffers independent of the immutable measurement retained by the snapshot.
    curve = plot.plot(x.copy(), y.copy(), pen=pen, antialias=True, connect=connect)
    register_stroke(curve, 1.25, plot)
    curve.setZValue(-5)
    curve.setProperty("storedColor", color.name())  # Keep each channel tint intact when its fade is refreshed.
    curve.setProperty("storedAlpha", color.alpha())
    curve.setDynamicRangeLimit(2.0, hysteresis=1.0)
    curve.curve.setSegmentedLineMode("on" if measurement in SPECTRUM_MODES else "off")
    fade_stored_curve(curve, *plot.getViewBox().viewRange()[0])
    return curve


def copy_plot(plot, sources, measurement, view):
    """Copy displayed harmonic paths into thinner channel-tinted curves behind the live data.

    Args:
        plot: PlotWidget that owns the new graphics items.
        sources: Live channel paths to snapshot.
        measurement: Current measurement identifier.
        view: Current axis representation identifier.
    """
    curves = []
    for source in sources:
        if source.isVisible() and source.xData is not None:
            curves.append(add_stored_curve(plot, source.xData, source.yData, source.opts["pen"],
                                           measurement, source.opts["connect"]))
    return StoredPlot(measurement, view, tuple(curves))


def capture_response(plot, channels, linked, view, ms_mode=False):
    """Freeze all response information and render the initially selected representation.

    Args:
        plot: PlotWidget that owns the snapshot's graphics items.
        channels: Absolute or relative complex channel responses currently displayed.
        linked: Whether one common trace represents the displayed channels.
        view: Initial response representation identifier.
        ms_mode: Use Mid/Side channel colors instead of Left/Right.
    """
    stored = StoredPlot("frequency", "", (), deepcopy(channels), linked)
    stored.ms_mode = ms_mode
    render_stored_response(plot, stored, view)
    return stored


def render_stored_response(plot, stored, view):
    """Redraw a frozen frequency response in another representation without using live data.

    Args:
        plot: PlotWidget owning the snapshot's graphics items.
        stored: Snapshot containing frozen complex responses and its currently rendered paths.
        view: Desired magnitude, wrapped/unwrapped phase, or sample-delay representation.
    """
    # Replace graphics items, keeping the underlying capture unchanged and item count bounded per snapshot.
    for curve in stored.curves:
        curve.hide()
        plot.removeItem(curve)
    curves = []
    channels = stored.responses[:1] if stored.linked else stored.responses
    for index, channel in enumerate(channels):
        frequency = channel.frequencies_hz[1:]
        values = display_values(channel, view)[1:]
        pen = pg.mkPen(MS_COLORS[index] if stored.ms_mode else TRACE_COLORS[0 if stored.linked else index + 1])
        if view == "wrapped":
            # Reconstruct both solid phase and faint dotted wrap paths from the frozen phase data.
            solid_x, solid_y, connector_x, connector_y = wrapped_phase_paths(frequency, values)
            curves.append(add_stored_curve(plot, solid_x, solid_y, pen, "frequency"))
            if connector_x.size:
                pen.setStyle(Qt.DotLine)
                color = pen.color()
                color.setAlphaF(0.35)
                pen.setColor(color)
                curves.append(add_stored_curve(plot, connector_x, connector_y, pen, "frequency"))
        else:
            curves.append(add_stored_curve(plot, frequency, values, pen, "frequency"))
    stored.curves = tuple(curves)
    stored.view = view
