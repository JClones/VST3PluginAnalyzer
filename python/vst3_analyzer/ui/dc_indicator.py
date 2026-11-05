# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Explicit DC annotations at the boundary of a logarithmic frequency plot."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt

from .plot_strokes import register_stroke


class DcIndicator:
    """Draw DC separately from measured positive-frequency coordinates."""

    def __init__(self, plot, cursor):
        self.plot, self.cursor = plot, cursor
        self.records = []  # First frequency, DC value, first-bin value and channel pen.
        self.live_values = None
        self.items = []
        self.label = pg.TextItem("DC", color="#000000", anchor=(0, 1), fill="#ffffff")
        self.label.setZValue(9)
        self.label.setAcceptedMouseButtons(Qt.NoButton)
        plot.addItem(self.label, ignoreBounds=True)
        plot.getViewBox().sigRangeChanged.connect(self.refresh)
        self.refresh()

    def set_data(self, records, live_values=None):
        self.records, self.live_values = records, live_values
        while len(self.items) > len(records):
            for item in self.items.pop():
                self.plot.removeItem(item)
        while len(self.items) < len(records):
            line = pg.PlotDataItem(pen=pg.mkPen("#000000"), antialias=True)
            marker = pg.ScatterPlotItem(size=7, pxMode=True, useCache=False)  # Keep SVG symbols vector-based.
            for item in (line, marker):
                item.setAcceptedMouseButtons(Qt.NoButton)
                self.plot.addItem(item, ignoreBounds=True)
            register_stroke(line, 1.25, self.plot)
            self.items.append((line, marker))
        self.refresh()

    def refresh(self):
        box = self.plot.getViewBox()
        (left, right), (bottom, top) = box.viewRange()
        edge = left + 4 * (right-left) / max(1, box.width())  # Keep the symbol clear of the plot border.
        visible = False
        for (frequency, dc, first, source_pen), (line, marker) in zip(self.records, self.items):
            x = np.log10(frequency)
            shown = bool(left - 1e-12 <= x)  # Keep the connector when bin one is off-screen to the right.
            visible |= shown
            line.hide()
            marker.hide()
            if not shown:
                continue
            pen = pg.mkPen(source_pen)
            pen.setStyle(Qt.DashLine)
            pen.setWidthF(1.25 * self.plot.devicePixelRatioF())
            line.setPen(pen)
            # Coordinates here are already logarithmic; zero is never placed on the frequency axis.
            line.setLogMode(False, False)
            if np.isfinite(dc):
                y = float(np.clip(dc, bottom, top))
                symbol = "t" if dc < bottom else "t1" if dc > top else "o"
                marker.setData([edge], [y], symbol=symbol, pen=pen, brush=pg.mkBrush(pen.color()))
                marker.show()
                if np.isfinite(first):
                    # Keep actual endpoints: the viewport clips connectors to off-screen DC values.
                    line.setData([edge, x], [dc, first], connect="finite")
                    line.show()
        self.label.setVisible(visible)
        self.label.setPos(edge, bottom)
        self.cursor.set_dc_data(self.live_values if visible else None, edge)
