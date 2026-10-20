# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Mouse-frequency guide and live, interpolated main-curve values."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt
from ..dsp.reconstruction import SincInterpolator
from .impulse_curve import RECONSTRUCTION_SPAN_MS
from .plot_strokes import register_stroke


class PlotCursor(QObject):
    """Keep cursor overlays independent of measurement acquisition and navigation."""

    def __init__(self, plot, readout):
        """Attach overlays; plot is the FFT widget and readout is its bordered label."""
        super().__init__(plot)
        self.plot = plot  # Canvas providing logarithmic coordinates and mouse events.
        self.readout = readout  # Screen-sized panel anchored inside the data area.
        self.ms_mode = False  # Select Mid/Side labels for decoded channel values.
        self.enabled = False  # User preference, independent of available measurement data.
        self.position = None  # Last scene position while the mouse remains inside the viewport.
        self.data = None  # Frequency bins, channel values, linkage and selected representation.
        # Longer explicit dashes remain distinct from dots on high-resolution displays.
        guide_pen = pg.mkPen('#333333', width=0.75)
        guide_pen.setDashPattern([8, 5])
        self.line = pg.InfiniteLine(pen=guide_pen, movable=False)  # Frequency guide.
        self.horizontal_lines = tuple(pg.InfiniteLine(angle=0, pen=guide_pen, movable=False)
                                      for _ in range(2))  # One value guide per displayed channel.
        for guide in (self.line, *self.horizontal_lines):
            register_stroke(guide, 0.75, plot)
            # Guides remain above curves without capturing input or influencing automatic ranges.
            guide.setAcceptedMouseButtons(Qt.NoButton)
            guide.setZValue(10)
            plot.addItem(guide, ignoreBounds=True)
        readout.setZValue(20)
        readout.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(12, 12))
        readout.setAcceptedMouseButtons(Qt.NoButton)
        plot.scene().sigMouseMoved.connect(self.mouse_moved)
        plot.pointer_left.connect(self.mouse_left)
        plot.getViewBox().sigRangeChanged.connect(self.refresh)
        self.refresh()

    def set_enabled(self, enabled):
        """Show or hide tracking according to the boolean menu preference enabled."""
        self.enabled = enabled
        self.refresh()

    def mouse_moved(self, position):
        """Track position in scene coordinates without consuming mouse gestures."""
        self.position = position
        self.refresh()

    def mouse_left(self):
        """Hide overlays when the pointer leaves the plot viewport."""
        self.position = None
        self.refresh()

    def set_data(self, coordinates, values, linked, view):
        """Cache coordinates (Hz or ms), displayed channel values, linkage and view identifier."""
        self.data = (coordinates if view == 'impulse' else np.log10(coordinates), values, linked, view)
        self.refresh()

    def clear(self):
        """Discard measurement values while retaining the tracking preference."""
        self.data = None
        self.refresh()

    def refresh(self):
        """Update the guide and interpolated values, including under a stationary pointer."""
        box = self.plot.getViewBox()
        visible = (self.enabled and self.data is not None and self.position is not None
                   and box.sceneBoundingRect().contains(self.position))
        self.line.setVisible(bool(visible))
        self.readout.setVisible(bool(visible))
        for guide in self.horizontal_lines:
            guide.hide()
        if not visible:
            return
        # Interpolate in logarithmic X to match the straight segments drawn between FFT bins.
        x = box.mapSceneToView(self.position).x()
        bins, channels, linked, view = self.data
        self.line.setPos(x)
        unit = {'magnitude': 'dB', 'wrapped': '°', 'unwrapped': '°', 'delay': 'samples', 'impulse': ''}[view]
        time = 0.0 if abs(x) < 0.0005 else x  # Suppress a negative zero caused by scene-coordinate rounding.
        rows = [f'{time:.3f} ms' if view == 'impulse' else f'{10 ** x:.3f} Hz']
        for index, values in enumerate(channels[:1] if linked else channels):
            if view == 'impulse' and len(bins) > 1 and box.viewRange()[0][1] - box.viewRange()[0][0] < RECONSTRUCTION_SPAN_MS:
                # Evaluate the same continuous waveform at the exact pointer time, rather than rounding to display points.
                position = (x - bins[0]) / (bins[1] - bins[0])
                value = SincInterpolator(np.array([position])).evaluate(values)[0]
            else:
                value = self.interpolate(bins, values, x, view == 'wrapped')
            # Cross the frequency guide at each finite live curve value; linked stereo needs one line.
            if np.isfinite(value):
                self.horizontal_lines[index].setPos(value)
                self.horizontal_lines[index].show()
            prefix = ('Mid: ' if index == 0 else 'Side: ') if self.ms_mode else ('' if linked else ('Left: ' if index == 0 else 'Right: '))
            text = f'{value:.6f}' if view == 'impulse' else f'{value:.3f} {unit}'
            # Treat sub-floor magnitudes as silence in the readout without changing plot data.
            if view == 'magnitude' and value < -300:
                text = '−∞ dB'
            elif not np.isfinite(value):
                text = 'undefined'
            rows.append(prefix + text)
        self.readout.setText('<div style="padding:4px;">' + '<br>'.join(rows) + '</div>',
                             color='#000000', family='Arial', size='14px', bold=True)

    @staticmethod
    def interpolate(bins, values, x, wrapped):
        """Interpolate values at log-frequency x in bins; wrapped selects shortest phase travel."""
        # Missing bins remain undefined rather than inventing values across gaps or beyond Nyquist.
        if len(bins) == 0 or x < bins[0] or x > bins[-1]:
            return np.nan
        right = int(np.searchsorted(bins, x))
        if bins[right] == x:
            return values[right]
        left = right - 1
        if not np.isfinite(values[left:right + 1]).all():
            return np.nan
        difference = values[right] - values[left]
        if wrapped:
            difference = (difference + 180) % 360 - 180
        value = values[left] + difference * (x - bins[left]) / (bins[right] - bins[left])
        return (value + 180) % 360 - 180 if wrapped else value
