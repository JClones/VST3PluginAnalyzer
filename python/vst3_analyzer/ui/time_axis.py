# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Linear millisecond axis using the same labeled 1/2/5 grid as sample values."""
from PySide6.QtGui import QFontMetricsF
from .decibel_axis import DecibelAxis


class TimeAxis(DecibelAxis):
    """Keep negative times and decimal millisecond ticks readable while zooming."""

    def __init__(self):
        """Create a bottom numeric axis without SI rescaling or unlabeled minor ticks."""
        super().__init__(orientation="bottom")

    def tickValues(self, minVal, maxVal, size):
        """Choose 1/2/5 spacings between minVal and maxVal over size horizontal pixels."""
        # Horizontal labels need more room than vertical ones, especially for negative decimals.
        return super().tickValues(minVal, maxVal, size * 0.6)

    def boundingRect(self):
        """Include tick-label overhang in the available left-axis gutter."""
        bounds = super().boundingRect()
        font = self.style.get("tickFont") or self.font()
        return bounds.adjusted(-QFontMetricsF(font).horizontalAdvance("-100") / 2 - 2, 0, 0, 0)
