# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Logarithmic frequency axis retaining labels at the left viewport boundary."""
import pyqtgraph as pg
from PySide6.QtGui import QFontMetricsF


class FrequencyAxis(pg.AxisItem):
    """Allow the first centered tick label to extend into the available left-axis margin."""

    def __init__(self):
        """Create a bottom axis whose boundary labels are not automatically suppressed."""
        super().__init__(orientation="bottom")
        # The magnitude-axis gutter provides space for the left half of the boundary label.
        self.setStyle(hideOverlappingLabels=False)

    def boundingRect(self):
        """Include the left tick-label overhang even when grid drawing links the view bounds."""
        # AxisItem's grid bounds otherwise discard the label centered exactly on the left edge.
        bounds = super().boundingRect()
        font = self.style.get("tickFont") or self.font()
        margin = QFontMetricsF(font).horizontalAdvance("20") / 2 + 2
        return bounds.adjusted(-margin, 0, 0, 0)
