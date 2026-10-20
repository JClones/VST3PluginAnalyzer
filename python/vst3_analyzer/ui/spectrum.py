# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Compact white-background FFT view with sample-driven stereo trace colors."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPalette
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .comparisons import MS_COLORS, TRACE_COLORS, capture_response, copy_plot, display_values, fade_stored_curve, render_stored_response
from .decibel_axis import DecibelAxis
from .frequency_axis import FrequencyAxis
from .time_axis import TimeAxis
from .impulse_curve import ImpulseCurveRenderer
from .phase_curve import wrapped_phase_paths
from .plot_export import ExportTrace
from .plot_cursor import PlotCursor
from .navigation import AnalyzerPlotWidget, AnalyzerViewBox
from .plot_strokes import register_stroke
from ..dsp.distortion import combine_distortion
from ..dsp.response import relative_response
from ..measurements.modes import GENERATORS, MEASUREMENTS, SPECTRUM_MODES


class DistortionReadout(pg.LabelItem):
    """Screen-sized white distortion panel with a thin reference-style border."""

    def paint(self, painter, option, widget=None):
        """Paint an opaque panel behind the child text item."""
        # QTextDocument does not reliably render CSS backgrounds and borders on a div.
        view = self.getViewWidget()
        ratio = view.devicePixelRatioF() if view is not None else painter.device().devicePixelRatioF()
        painter.setPen(pg.mkPen("#000000", width=0.5 * ratio))
        painter.setBrush(pg.mkBrush("#ffffff"))
        painter.drawRect(self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5))


class SpectrumView(QWidget):
    """Render mono/equal stereo as magenta and mismatched stereo as blue-left/red-right."""

    comparison_state_changed = Signal()  # Availability of Store/Clear/reference commands has changed.

    def __init__(self, parent=None):
        """Create permanent curves and the logarithmic frequency axis once."""
        super().__init__(parent)
        # Keep the outer plot margin white regardless of the desktop's widget theme.
        palette = self.palette()
        palette.setColor(QPalette.Window, Qt.white)
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        # Keep all plot objects on the Qt GUI thread.
        self.frequency_axis = FrequencyAxis()  # Persistent logarithmic bottom axis for spectra.
        self.time_axis = TimeAxis()  # Persistent linear bottom axis for raw impulse samples.
        self.plot = AnalyzerPlotWidget(background="w", axisItems={"left": DecibelAxis(), "bottom": self.frequency_axis}, viewBox=AnalyzerViewBox())  # Main analysis canvas.
        label_style = {"color": "#000000", "font-family": "Arial", "font-size": "14px", "font-weight": "bold"}
        self.plot.setLabel("bottom", "Frequency [Hz]", **label_style)
        self.plot.setLabel("left", "Magnitude [dB]", **label_style)
        self.plot.setLogMode(x=True, y=False)
        self.plot.showGrid(x=True, y=True, alpha=0.45)
        # Delineate the data area before the white margin at the window's right edge.
        self.plot.getViewBox().setBorder(pg.mkPen("#000000", width=0.5 * self.plot.devicePixelRatioF()))
        # Fixed logarithmic labels avoid overlapping minor-tick text and automatic kHz rescaling.
        axis = self.plot.getAxis("bottom")
        axis.enableAutoSIPrefix(False)
        self.stored_plots = []  # Unbounded, session-only frozen pictures, grouped by measurement and view.
        self.reference = None  # Deep-copied absolute frequency-response frame, never persisted.
        self.response_view = "magnitude"  # Selected frequency-response representation.
        self.latest_frame = None  # Latest matching record, reused when changing only the view.
        self.ms_mode = False  # Channel interpretation shared with acquisition and numeric overlays.
        self.measurement = "harmonic"  # Determines default vertical bounds and readout visibility.
        self.latency_samples = 0  # Latest impulse-response alignment and negative-time extent.
        self.impulse_renderer = ImpulseCurveRenderer()  # Cached viewport sinc kernel shared by all impulse channels.
        self.set_sample_rate(44100.0)
        self.reset_view()
        self.plot.getViewBox().reset_requested.connect(self.reset_axes)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        # Anchor a screen-sized readout inside the canvas, independent of zoom and axis ranges.
        self.readout = DistortionReadout(parent=self.plot.getViewBox(), justify="left")  # Combined distortion box.
        self.readout.setZValue(20)
        self.readout.anchor(itemPos=(1, 0), parentPos=(1, 0), offset=(-12, 12))
        self.cursor = PlotCursor(self.plot, DistortionReadout(parent=self.plot.getViewBox(), justify="left"))
        # Match the reference's bold, compact axis typography at a fixed logical-pixel size.
        tick_font = QFont("Arial")
        tick_font.setPixelSize(14)
        tick_font.setBold(True)
        for name in ("bottom", "left"):
            self.plot.getAxis(name).setPen(pg.mkPen("#000000"))
            self.plot.getAxis(name).setTextPen(pg.mkPen("#000000"))
            self.plot.getAxis(name).setStyle(tickFont=tick_font)
            # Grid lines belong behind the ViewBox, otherwise they tint even opaque trace pixels.
            self.plot.getAxis(name).setZValue(self.plot.getViewBox().zValue() - 1)
        # Antialiased strokes use the Retina reference width expressed in logical pixels.
        self.linked = self.plot.plot(pen=pg.mkPen(TRACE_COLORS[0], width=1.75), antialias=True)  # Magenta linked trace.
        self.left = self.plot.plot(pen=pg.mkPen(TRACE_COLORS[1], width=1.75), antialias=True)  # Reference blue left channel.
        self.right = self.plot.plot(pen=pg.mkPen(TRACE_COLORS[2], width=1.75), antialias=True)  # Reference red right channel.
        self.wrap_connections = []  # Faint wrap-jump overlays matching linked, left and right trace colors.
        for color in TRACE_COLORS:
            # Separate overlays let wrap jumps use a lighter stroke without thinning the measured phase curve.
            pen = pg.mkPen(color, width=1.75, style=Qt.DotLine)
            tint = pen.color()
            tint.setAlphaF(0.35)
            pen.setColor(tint)
            connection = self.plot.plot(pen=pen, antialias=True)
            connection.setZValue(-1)
            self.wrap_connections.append(connection)
        # Bound off-screen drawing geometry while keeping the original spectrum available when zooming.
        for curve in (self.linked, self.left, self.right, *self.wrap_connections):
            # 1.75 logical pixels preserves the former 3.5-device-pixel Retina appearance.
            register_stroke(curve, 1.75, self.plot)
        for curve in (self.linked, self.left, self.right):
            # Rounded joins keep adjacent FFT-bin segments smooth at bends in the response.
            pen = curve.opts["pen"]
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setCapStyle(Qt.RoundCap)
            curve.setPen(pen)
            curve.setOpacity(1.0)
            curve.curve.setCompositionMode(QPainter.CompositionMode_SourceOver)
            curve.setDynamicRangeLimit(2.0, hysteresis=1.0)
            curve.curve.setSegmentedLineMode("on")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 12, 0)
        layout.addWidget(self.plot)
        self.clear()
        self.plot.getViewBox().sigXRangeChanged.connect(self._update_stored_fades)
        self.plot.getViewBox().sigXRangeChanged.connect(self._update_impulse_curves)

    def _update_impulse_curves(self):
        """Redraw live and frozen raw impulses when zooming, panning or receiving another frame."""
        if self.measurement != "impulse":
            return
        self.impulse_renderer.set_view(*self.plot.getViewBox().viewRange()[0], 2 * self.nyquist)
        # Reconstruct from complete retained records, including off-screen neighbors needed by the kernel.
        if self.latest_frame is not None and self.latest_frame.measurement == "impulse":
            frame = self.latest_frame
            curves = (self.linked,) if frame.linked else (self.left, self.right)
            for curve, channel in zip(curves, frame.channels):
                curve.setData(*self.impulse_renderer.points(channel), connect="finite")
        for stored in self.stored_plots:
            if stored.measurement == "impulse":
                for curve, channel in zip(stored.curves, stored.impulse_channels):
                    curve.setData(*self.impulse_renderer.points(channel), connect="finite")

    def _update_stored_fades(self):
        """Keep stored curves' edge fades aligned with the visible logarithmic frequency interval."""
        left, right = self.plot.getViewBox().viewRange()[0]
        for stored in self.stored_plots:
            for curve in stored.curves:
                fade_stored_curve(curve, left, right)

    def set_ms_mode(self, enabled):
        """Set enabled M/S channel interpretation and discard incompatible captures and comparisons."""
        self.ms_mode = bool(enabled)
        self.clear()
        self.cursor.ms_mode = self.ms_mode
        # Keep stroke geometry while changing both solid curves and their faint wrap connectors.
        colors = (MS_COLORS[0], *MS_COLORS) if self.ms_mode else TRACE_COLORS
        for curve, overlay, color in zip((self.linked, self.left, self.right), self.wrap_connections, colors):
            pen = curve.opts["pen"]
            pen.setColor(color)
            curve.setPen(pen)
            pen = overlay.opts["pen"]
            tint = pg.mkColor(color)
            tint.setAlphaF(0.35)
            pen.setColor(tint)
            overlay.setPen(pen)

    def set_measurement(self, measurement):
        """Select the plot defaults and discard traces from the previous stimulus."""
        # Switching measurements changes the view only; the worker owns audio acquisition.
        if measurement not in MEASUREMENTS and measurement not in GENERATORS:
            raise ValueError("Unknown measurement")
        self.measurement = measurement
        self._update_horizontal_axis()
        self._update_vertical_axis()
        # Draw response bins as one antialiased path, avoiding seams between independent thick segments.
        # Retain segmented rendering for the much taller, dense harmonic spectra to keep redraws responsive.
        for curve in (self.linked, self.left, self.right):
            curve.curve.setSegmentedLineMode("on" if measurement in SPECTRUM_MODES else "off")
        self.clear(reset_comparisons=False)
        self.reset_view()

    def set_response_view(self, view):
        """Select magnitude, wrapped/unwrapped phase or delay without restarting acquisition.

        Args:
            view: Stable frequency-response view identifier.
        """
        if view not in ("magnitude", "wrapped", "unwrapped", "delay"):
            raise ValueError("Unknown frequency-response view")
        self.response_view = view
        self._update_vertical_axis()
        self.reset_axes(False, True)
        if self.latest_frame is not None:
            self.show_frame(self.latest_frame)
        self._update_stored_visibility()

    def store_plot(self):
        """Freeze the full displayed response, or the current harmonic paths, independently of live buffers."""
        if self.latest_frame is None:
            return
        view = self.display_view()
        sources = (self.linked, self.left, self.right, *self.wrap_connections)
        if self.measurement == "frequency":
            channels, linked = self._display_channels(self.latest_frame)
            stored = capture_response(self.plot, channels, linked, view, self.ms_mode)
        else:
            from copy import deepcopy
            stored = copy_plot(self.plot, sources, self.measurement, view)
            if self.measurement == "impulse":
                stored.impulse_channels = deepcopy(self.latest_frame.channels)
            else:
                stored.harmonic_channels = deepcopy(self.latest_frame.channels)
            stored.linked = self.latest_frame.linked
        stored.ms_mode = self.ms_mode
        self.stored_plots.append(stored)
        self._update_impulse_curves()
        self.comparison_state_changed.emit()

    def export_traces(self):
        """Collect each visible live/stored channel at its original FFT bins, without wrap decorations."""
        view = self.display_view()
        traces = []

        def add_channels(name, channels, linked, ms_mode=False):
            """Append original-bin values for one visible measurement capture.

            Args:
                name: Live or stored plot label for the CSV headings.
                channels: Full-bin response/spectrum results belonging to that capture.
                linked: Whether a common trace represents both displayed stereo channels.
                ms_mode: Label channels as Mid and Side.
            """
            labels = ("Mono" if len(channels) == 1 else "Linked",) if linked else ("Left", "Right")
            if ms_mode:
                labels = ("Mid", "Side")
            for channel, label in zip(channels, labels):
                coordinates = channel.times_ms if view == "impulse" else channel.frequencies_hz
                traces.append(ExportTrace(f"{name} {label}", coordinates, display_values(channel, view)))

        # Export data for every FFT bin, even when the viewport zoom clips part of a visible trace.
        if self.latest_frame is not None:
            channels, linked = self._display_channels(self.latest_frame)
            add_channels("Main", channels, linked, self.ms_mode)
        for index, stored in enumerate(self.stored_plots, 1):
            if stored.measurement == self.measurement and any(curve.isVisible() for curve in stored.curves):
                channels = (stored.responses if stored.measurement == "frequency" else
                            stored.impulse_channels if stored.measurement == "impulse" else stored.harmonic_channels)
                if channels:
                    add_channels(f"Stored {index}", channels, stored.linked, stored.ms_mode)
        return traces

    def clear_stored(self):
        """Remove every stored graphics item while retaining the live plot and reference."""
        for stored in self.stored_plots:
            for curve in stored.curves:
                self.plot.removeItem(curve)
        self.stored_plots.clear()
        self.comparison_state_changed.emit()

    def make_reference(self):
        """Capture the current absolute response and redraw its difference immediately."""
        if self.measurement != "frequency" or self.latest_frame is None:
            return
        # Replacing an existing reference always captures raw data, never the already relative display.
        from copy import deepcopy
        self.reference = deepcopy(self.latest_frame)
        self.show_frame(self.latest_frame)

    def clear_reference(self):
        """Return to absolute response display without resetting the processor or acquisition."""
        self.reference = None
        if self.latest_frame is not None:
            self.show_frame(self.latest_frame)
        self.comparison_state_changed.emit()

    def _update_stored_visibility(self):
        """Render every matching snapshot in the selected view, independently of the live response."""
        view = self.display_view()
        for stored in self.stored_plots:
            visible = stored.measurement == self.measurement
            if visible and stored.responses and stored.view != view:
                render_stored_response(self.plot, stored, view)
            for curve in stored.curves:
                curve.setVisible(visible)
        self._update_impulse_curves()

    def _update_vertical_axis(self):
        """Apply the units for the current measurement and selected representation."""
        # Harmonic spectra always retain their magnitude axis, independently of the saved response view.
        view = self.display_view()
        # Wrapped phase is bounded; other representations retain unrestricted vertical navigation.
        self.plot.getViewBox().setLimits(yMin=-180 if view == "wrapped" else None,
                                         yMax=180 if view == "wrapped" else None)
        label = "Phase [°]" if view in ("wrapped", "unwrapped") else ("" if view == "delay" else "Magnitude [dB]")
        if view == "impulse":
            label = "Sample value"
        self.plot.setLabel("left", label, color="#000000", **{
            "font-family": "Arial", "font-size": "14px", "font-weight": "bold"})

    def default_y_range(self):
        """Return the vertical reset interval for the active measurement and representation."""
        if self.measurement in SPECTRUM_MODES:
            return (-200, 5)
        if self.measurement == "impulse":
            return (-1, 1)
        if self.response_view in ("wrapped", "unwrapped"):
            return (-180, 180)
        if self.response_view == "delay":
            return (-10, 10)
        return (-20, 20)

    def zoom(self, factor, modifiers=Qt.NoModifier):
        """Scale the visible axes about their center using the same bounds as pinch zoom.

        Args:
            factor: Visible-span multiplier, below one to zoom in or above one to zoom out.
            modifiers: Optional Shift for Y-only or Alt/Option for X-only zoom.
        """
        box = self.plot.getViewBox()
        box.zoom_touchpad(factor, box.viewRect().center(), modifiers)

    def zoom_full(self, fft_size):
        """Show every non-DC FFT bin and all finite plotted Y values.

        Args:
            fft_size: Current measurement record length, used to locate FFT bin one.
        """
        # Fit measured traces, excluding the decorative wrap connectors and undefined bins.
        low, high = self.default_y_range()
        finite = []
        stored_curves = [curve for stored in self.stored_plots for curve in stored.curves]
        x_range = (float(np.log10(2 * self.nyquist / fft_size)), float(np.log10(self.nyquist)))
        arrays = []
        if self.measurement == "impulse":
            # Dense display points cover only the current viewport; Full must still inspect the complete captures.
            channels = list(self.latest_frame.channels) if self.latest_frame is not None else []
            channels.extend(channel for stored in self.stored_plots if stored.measurement == "impulse"
                            for channel in stored.impulse_channels)
            x_range = list(self.default_x_range())
            for channel in channels:
                x_range[0] = min(x_range[0], float(channel.times_ms[0]))
                x_range[1] = max(x_range[1], float(channel.times_ms[-1]))
            renderer = ImpulseCurveRenderer()
            renderer.set_view(*x_range, 2 * self.nyquist)
            for channel in channels:
                arrays.extend((channel.samples, renderer.points(channel)[1]))
        else:
            arrays = [curve.yData for curve in (self.linked, self.left, self.right, *stored_curves)
                      if curve.isVisible() and curve.yData is not None]
        for array in arrays:
            values = array[np.isfinite(array)]
            if values.size:
                finite.append((float(values.min()), float(values.max())))
        if finite:
            minimum = min(item[0] for item in finite)
            maximum = max(item[1] for item in finite)
            if maximum - minimum >= high - low:
                low, high = minimum, maximum
            else:
                # Retain the default span, shifting it only when needed to include outlying data.
                span = high - low
                low = min(low, minimum)
                low = max(low, maximum - span)
                high = low + span
            # Reserve room for half the thick stroke, the border and antialiasing in screen pixels.
            # Expand only where an extremum would otherwise touch the frame; keep the X bounds exact.
            height = max(16.0, self.plot.getViewBox().height())
            stroke_margin = 4.0
            margin = (high - low) * stroke_margin / (height - 2 * stroke_margin)
            low = min(low, minimum - margin)
            high = max(high, maximum + margin)
        self.plot.getViewBox().setRange(xRange=x_range, yRange=(low, high), padding=0)

    def reset_view(self):
        """Restore the requested default bounds without modifying analysis or audio history."""
        # Share the same default ranges with the directional reset gestures.
        self.reset_axes(True, True)

    def reset_axes(self, reset_x, reset_y):
        """Restore only the requested axes, preserving the other axis and audio history."""
        # No padding or autorange: resetting a single axis must not move the other one.
        ranges = {}
        if reset_x:
            ranges["xRange"] = self.default_x_range()
        if reset_y:
            ranges["yRange"] = self.default_y_range()
        self.plot.getViewBox().setRange(**ranges, padding=0)

    def set_sample_rate(self, sample_rate, fft_size=None):
        """Update Nyquist and the default left edge from the measurement configuration.

        Args:
            sample_rate: Current sample rate in Hz.
            fft_size: Optional new FFT record length; omitted values retain the current size.
        """
        # Stored plots and references share FFT bins; reset both when either grid-defining setting changes.
        if hasattr(self, "nyquist") and (self.nyquist != sample_rate / 2 or
                (fft_size is not None and self.fft_size != fft_size)):
            self.reference = None
            if hasattr(self, "linked"):
                self.clear(reset_comparisons=True)
        # ViewBox limits also constrain panning, View All and restored zoom ranges.
        self.fft_size = fft_size if fft_size is not None else getattr(self, "fft_size", 8192)
        self.default_frequency_min = 5 * (int((sample_rate / self.fft_size) // 5) + 1)  # Strictly above bin one.
        self.nyquist = sample_rate / 2  # Current maximum meaningful displayed frequency.
        # Include the dynamically chosen boundary as a labeled major tick.
        axis = self.frequency_axis
        frequencies = [5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000,
                       50000, 100000, 200000, 500000]
        frequencies = sorted(set(frequencies + [self.default_frequency_min]))
        major = [(np.log10(value), f"{value:g}" if value < 1000 else f"{value / 1000:g}k")
                 for value in frequencies]
        minor = [(np.log10(multiplier * 10 ** decade), "") for decade in range(0, 6)
                 for multiplier in range(1, 10) if multiplier * 10 ** decade not in frequencies]
        axis.setTicks([major, minor])
        self._update_horizontal_axis()

    def display_view(self):
        """Return the active data representation independently of saved frequency-view preferences."""
        if self.measurement == "frequency":
            return self.response_view
        return "impulse" if self.measurement == "impulse" else "magnitude"

    def default_x_range(self):
        """Return logarithmic frequency bounds or the complete latency-aligned time interval."""
        if self.measurement == "impulse":
            milliseconds = 1000.0 / (2 * self.nyquist)
            return (-self.latency_samples * milliseconds, (self.fft_size - 1) * milliseconds)
        return (float(np.log10(self.default_frequency_min)), float(np.log10(self.nyquist)))

    def _update_horizontal_axis(self):
        """Switch between logarithmic frequency and linear milliseconds without altering audio."""
        impulse = self.measurement == "impulse"
        axis = self.time_axis if impulse else self.frequency_axis
        if self.plot.getAxis("bottom") is not axis:
            self.plot.getPlotItem().setAxisItems({"bottom": axis})
        self.plot.setLogMode(x=not impulse, y=False)
        # Reapply styling after replacing the axis; PlotItem installs its own default Z order.
        font = QFont("Arial")
        font.setPixelSize(14)
        font.setBold(True)
        axis.setStyle(tickFont=font)
        axis.setPen(pg.mkPen("#000000"))
        axis.setTextPen(pg.mkPen("#000000"))
        axis.setZValue(self.plot.getViewBox().zValue() - 1)
        self.plot.setLabel("bottom", "Time [ms]" if impulse else "Frequency [Hz]", color="#000000",
                           **{"font-family": "Arial", "font-size": "14px", "font-weight": "bold"})
        self.plot.showGrid(x=True, y=True, alpha=0.45)
        low, high = self.default_x_range()
        # Checkbox values may be unchanged after an axis swap, so explicitly update the new axis grid.
        self.plot.getPlotItem().updateGrid()
        self.plot.getViewBox().default_x_min = low
        self.plot.getViewBox().setLimits(xMin=-float("inf"), xMax=high)

    def clear(self, reset_comparisons=True):
        """Remove invalid live data and optionally reset the loaded plugin's comparisons.

        Args:
            reset_comparisons: Clear snapshots and reference on unload/reload, but retain them on view changes.
        """
        # Preserve the user's zoom and axis settings while clearing stale data.
        for curve in (self.linked, self.left, self.right, *self.wrap_connections):
            curve.clear()
            curve.hide()
        self.readout.hide()
        self.latest_frame = None
        self.cursor.clear()
        if reset_comparisons:
            self.reference = None
            self.clear_stored()
        self._update_stored_visibility()
        self.comparison_state_changed.emit()

    def _display_channels(self, frame):
        """Return absolute or reference-relative channels and their displayed stereo linkage.

        Args:
            frame: Latest absolute live response, never mutated by comparison display.
        """
        channels, linked = frame.channels, frame.linked
        if frame.measurement == "frequency" and self.reference is not None:
            reference = self.reference.channels
            if len(reference) != len(channels) or not np.array_equal(reference[0].frequencies_hz, channels[0].frequencies_hz):
                self.reference = None
            else:
                channels = tuple(relative_response(channel, baseline) for channel, baseline in zip(channels, reference))
                # An asymmetric reference can split linked raw outputs into unequal relative responses.
                linked = len(channels) == 1 or np.array_equal(channels[0].spectrum, channels[1].spectrum, equal_nan=True)
        if frame.ms_mode and len(channels) == 2:
            linked = False
        return channels, linked

    def show_frame(self, frame):
        """Update the active trace set according to exact time-domain channel equality."""
        # DC is excluded from the logarithmic axis; zero handling does not alter analysis data.
        channels = frame.channels
        if frame.measurement != self.measurement or frame.ms_mode != self.ms_mode:
            return  # Ignore an already queued frame from the previous measurement.
        self.latest_frame = frame
        channels, linked = self._display_channels(frame)
        if self.measurement == "harmonic":
            # Calculate from full-precision spectra before applying the visual magnitude floor.
            fundamental_bin = int(np.argmin(np.abs(channels[0].frequencies_hz - frame.actual_frequency_hz)))
            values = combine_distortion(channels, fundamental_bin)
            thd = "undefined" if values.thd_db is None else f"{values.thd_db:.2f} dB"
            thdn = "undefined" if values.thdn_db is None else f"{values.thdn_db:.2f} dB"
            # Apply the display threshold in percent; keep the underlying distortion calculation unchanged.
            if values.klirr_percent is None:
                klirr = "undefined"
            elif values.klirr_percent < 1e-6:
                klirr = "0 %"
            else:
                klirr = f"{values.klirr_percent:.6f}".rstrip("0").rstrip(".") + " %"
            self.readout.setText(
                f'<div style="background-color:white; border:1px solid #000000; padding:4px;">'
                f'THD: {thd}<br>THD+N: {thdn}<br>Klirr: {klirr}</div>',
                color="#000000", family="Arial", size="14px", bold=True)
            self.readout.show()
        else:
            self.readout.hide()
        view = self.display_view()
        if view == "impulse":
            # Reveal the latency prefix on the first record, preserving any user zoom thereafter.
            was_default = np.allclose(self.plot.getViewBox().viewRange()[0], self.default_x_range())
            if self.latency_samples != frame.latency_samples:
                self.latency_samples = frame.latency_samples
                self._update_horizontal_axis()
                if was_default:
                    self.reset_axes(True, False)
            frequency = channels[0].times_ms
            amplitudes = [channel.samples for channel in channels]
        else:
            frequency = channels[0].frequencies_hz[1:]
            amplitudes = [display_values(channel, view)[1:] for channel in channels]
        self.cursor.set_data(frequency, amplitudes, linked, view)
        # Refresh only the active channel curves; clear overlays whenever the view or stereo linkage changes.
        for curve in (self.linked, self.left, self.right, *self.wrap_connections):
            curve.hide()
        active = ((self.linked, self.wrap_connections[0], amplitudes[0]),) if linked else (
            (self.left, self.wrap_connections[1], amplitudes[0]),
            (self.right, self.wrap_connections[2], amplitudes[1]))
        wrapped = self.measurement == "frequency" and self.response_view == "wrapped"
        for curve, overlay, values in active:
            if wrapped:
                # Draw every interpolated wrap segment faintly, including the legs to the phase boundaries.
                solid_x, solid_y, connector_x, connector_y = wrapped_phase_paths(frequency, values)
                curve.setData(solid_x, solid_y, connect="finite")
                if connector_x.size:
                    overlay.setData(connector_x, connector_y, connect="finite")
                    overlay.show()
            elif view != "impulse":
                curve.setData(frequency, values, connect="finite")
            curve.show()
        self._update_stored_visibility()
        self.comparison_state_changed.emit()
