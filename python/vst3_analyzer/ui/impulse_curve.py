# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Viewport-dependent impulse rendering with cached 256-tap sinc display reconstruction."""
import numpy as np
from ..dsp.reconstruction import SincInterpolator


RECONSTRUCTION_SPAN_MS = 10.0  # Reconstruct only when strictly less than this duration is visible.
RECONSTRUCTION_POINTS = 1024  # Evenly spaced horizontal display positions, independent of captured sample count.


class ImpulseCurveRenderer:
    """Share one viewport kernel across live and stored impulse-response channels."""

    def __init__(self):
        """Start without cached coordinates; the owning view supplies its current range."""
        self.view_key = None  # Last (left ms, right ms, sample rate Hz) configuration.
        self.times_ms = None  # Dense display coordinates for a zoomed-in viewport.
        self.kernel = None  # Reusable sinc weights, or None for ordinary raw-sample display.
        self.sample_rate = None  # Current sample rate in Hz for latency-origin conversion.

    def set_view(self, left, right, sample_rate):
        """Prepare the visible interval from left to right milliseconds at sample_rate Hz."""
        key = (left, right, sample_rate)
        if key == self.view_key:
            return
        self.view_key = key
        self.sample_rate = sample_rate
        # Keep the familiar sample-connected overview at 10 ms and above, including exactly 10 ms.
        self.kernel = None
        self.times_ms = None
        if 0 < right - left < RECONSTRUCTION_SPAN_MS:
            self.times_ms = np.linspace(left, right, RECONSTRUCTION_POINTS, dtype=np.float64)
            self.kernel = SincInterpolator(self.times_ms * (sample_rate / 1000.0))

    def points(self, response):
        """Return display coordinates and values for response without modifying its raw arrays."""
        if self.kernel is None:
            return response.times_ms, response.samples
        # All captures share the time-zero sample grid even when their retained latency prefixes differ.
        origin = int(round(response.times_ms[0] * self.sample_rate / 1000.0))
        return self.times_ms, self.kernel.evaluate(response.samples, origin)
