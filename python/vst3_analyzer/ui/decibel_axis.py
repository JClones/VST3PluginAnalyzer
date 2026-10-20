# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""A fully labeled numeric grid shared by magnitude, phase and sample-delay views."""
import math
import pyqtgraph as pg
from PySide6.QtGui import QFontMetricsF


class DecibelAxis(pg.AxisItem):
    """Choose 1/2/5 decimal spacings for magnitudes, degrees or samples, labeling every line."""

    def __init__(self, orientation="left", **kwargs):
        """Create a numeric axis; orientation selects its edge and kwargs configure AxisItem."""
        super().__init__(orientation=orientation, **kwargs)
        # Every generated grid tick belongs to the primary labeled level.
        self.enableAutoSIPrefix(False)
        self.setStyle(maxTickLevel=0, maxTextLevel=0, hideOverlappingLabels=False)

    def tickValues(self, minVal, maxVal, size):
        """Select a readable 1/2/5 step using the viewport height in logical pixels."""
        # Aim for approximately 48 pixels between labels, refining as the view gets taller or zooms in.
        minimum, maximum = sorted((minVal, maxVal))
        span = maximum - minimum
        if not math.isfinite(span) or span <= 0 or size <= 0:
            return []
        font = self.style.get("tickFont") or self.font()
        minimum_gap = QFontMetricsF(font).height() + 2
        # Extend the established coarse steps with decimal powers as far as float64 can distinguish.
        target = max(48 * (span / size), math.ulp(max(abs(minimum), abs(maximum))))
        exponent = max(-323, min(306, math.floor(math.log10(target))))
        supported = [factor * 10.0 ** power for power in range(max(-323, exponent - 1), exponent + 2)
                     for factor in (1, 2, 5)]
        resolution = math.ulp(max(abs(minimum), abs(maximum)))
        candidates = [step for step in supported if step >= resolution and step / span * size >= minimum_gap]
        step = min(candidates or [target], key=lambda value: abs(value / span * size - 48))

        # Use integer tick indices to avoid accumulated fractional-step rounding errors.

        first = math.ceil(minimum / step)
        last = math.floor(maximum / step)
        return [(step, [index * step for index in range(first, last + 1)])]

    def tickStrings(self, values, scale, spacing):
        """Label every tick numerically without SI prefixes or fractional noise."""
        # Fixed decimal precision tracks the grid step and suppresses floating-point artifacts.
        decimals = max(0, int(-math.floor(math.log10(spacing)))) if spacing > 0 else 0
        return [f"{0.0 if abs(value) < spacing / 2 else value:.{decimals}f}" for value in values]
