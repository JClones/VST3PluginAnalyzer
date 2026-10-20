# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""PNG, vector SVG and full-precision, per-bin CSV export of the current plot."""
import csv
from dataclasses import dataclass
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import numpy as np


EXPORT_FILTERS = "PNG image (*.png);;SVG image (*.svg);;CSV data (*.csv)"  # Native Save As format choices.


@dataclass(frozen=True)
class ExportTrace:
    """One displayed channel on its original FFT grid, without decorative wrap points."""

    name: str  # Live or stored plot identifier and channel name.
    coordinates: np.ndarray  # Full FFT-bin frequencies in Hz, or raw sample times in milliseconds.
    values: np.ndarray  # Selected view's values at those bins, with NaN for undefined results.


def export_path(filename, selected_filter):
    """Resolve an explicit supported suffix or append the selected format's suffix.

    Args:
        filename: User-selected Save As path, possibly without an extension.
        selected_filter: Native dialog format filter; PNG is the fallback.
    """
    path = Path(filename)
    suffix = path.suffix.lower()
    if not suffix:
        suffix = ".svg" if "*.svg" in selected_filter else ".csv" if "*.csv" in selected_filter else ".png"
        path = Path(str(path) + suffix)
    if suffix not in (".png", ".svg", ".csv"):
        raise ValueError("Choose a PNG, SVG or CSV filename")
    return path


def write_csv(path, traces, view):
    """Write every original FFT bin of every visible trace, without resampling.

    Args:
        path: Destination CSV path.
        traces: Live and stored channels to export in display order.
        view: Selected response representation, used for the value-column units.
    """
    units = {"magnitude": "Magnitude [dB]", "wrapped": "Phase wrapped [°]",
             "unwrapped": "Phase unwrapped [°]", "delay": "Group Delay [samples]", "impulse": "Sample value"}[view]
    # A shared frequency column is valid only when all visible captures use the same original bin grid.
    traces = tuple(traces)
    coordinates = traces[0].coordinates if traces else np.array([])
    if view == "impulse" and traces:
        # Different captured latencies can extend the negative prefix; keep every original sample.
        coordinates = np.unique(np.concatenate([trace.coordinates for trace in traces]))
    elif any(not np.array_equal(trace.coordinates, coordinates) for trace in traces):
        raise ValueError("Visible plots use different FFT frequency grids; clear incompatible stored plots")
    columns = []
    for trace in traces:
        values = np.full(coordinates.size, np.nan)
        values[np.searchsorted(coordinates, trace.coordinates)] = trace.values
        columns.append(values)
    with Path(path).open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        headers = ["Sample", "Time [ms]"] if view == "impulse" else ["FFT bin", "Frequency [Hz]"]
        for trace in traces:
            if trace.name in ("Main Mono", "Main Linked"):
                headers.append(units)
            else:
                name = trace.name.removeprefix("Main ")
                headers.append(f"{name} {units}")
        writer.writerow(headers)
        for index, coordinate in enumerate(coordinates):
            row = [index, format(float(coordinate), ".17g")]
            for column in columns:
                # Seventeen significant digits round-trip float64; undefined phase/reference bins stay blank.
                value = column[index]
                row.append(format(float(value), ".17g") if np.isfinite(value) else "")
            writer.writerow(row)


def repair_svg_clipping(root, definitions, widget, trace_colors):
    """Replace leaked QPicture clip state with explicit widget and plot-area clipping.

    Args:
        root: Parsed SVG document root.
        definitions: SVG defs element receiving the replacement clipping rectangles.
        widget: SpectrumView supplying the rendered widget and plot-area geometry.
        trace_colors: Live and stored stroke colors whose paths belong inside the plot area.
    """
    namespace = "http://www.w3.org/2000/svg"
    # Cached axis QPictures can leave the previous label's clip active in Qt's SVG paint engine.
    # Labels/frame belong to the widget; grid and response paths belong to the data rectangle.
    for old in list(definitions):
        if old.tag == f"{{{namespace}}}clipPath":
            definitions.remove(old)
    view = widget.plot
    source = view.viewport().rect()
    target = view.geometry()
    area = view.viewportTransform().mapRect(view.getViewBox().sceneBoundingRect())
    scale_x, scale_y = target.width() / source.width(), target.height() / source.height()
    plot_rectangle = (target.x() + (area.left() - source.left()) * scale_x,
                      target.y() + (area.top() - source.top()) * scale_y,
                      area.width() * scale_x, area.height() * scale_y)
    for identifier, rectangle in (("export-widget-clip", (0, 0, widget.width(), widget.height())),
                                   ("export-plot-clip", plot_rectangle)):
        clip = ET.SubElement(definitions, f"{{{namespace}}}clipPath", {
            "id": identifier, "clipPathUnits": "userSpaceOnUse"})
        ET.SubElement(clip, f"{{{namespace}}}rect", dict(zip(
            ("x", "y", "width", "height"), (format(value, ".17g") for value in rectangle))))
    parents = {child: parent for parent in root.iter() for child in parent}
    for element in list(root.iter()):
        if "clip-path" in element.attrib:
            element.set("clip-path", "url(#export-widget-clip)")
    for element in list(root.iter(f"{{{namespace}}}g")):
        stroke = element.get("stroke")
        is_grid = (stroke in ("#000000", "black") and 0 < float(element.get("stroke-opacity", "1")) < 1
                   and any(child.tag.rsplit("}", 1)[-1] in ("path", "polyline", "line") for child in element))
        if stroke not in trace_colors and not is_grid:
            continue
        # Qt writes each transformed paint group inside an untransformed clip wrapper.
        parent = parents[element]
        if "clip-path" in parent.attrib:
            parent.set("clip-path", "url(#export-plot-clip)")
        else:
            position = list(parent).index(element)
            parent.remove(element)
            wrapper = ET.Element(f"{{{namespace}}}g", {"clip-path": "url(#export-plot-clip)"})
            wrapper.append(element)
            parent.insert(position, wrapper)


def finalize_svg(path, widget):
    """Set pixel dimensions and restore gradient strokes omitted by Qt's SVG writer.

    Args:
        path: Completed SVG 1.1 document to update before committing it.
        widget: SpectrumView providing visible snapshot colors and logarithmic viewport bounds.
    """
    # Pixel dimensions keep the document's intrinsic display size independent of the display's logical DPI.
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    tree = ET.parse(path)
    root = tree.getroot()
    root.set("width", str(widget.width()))
    root.set("height", str(widget.height()))
    # Cosmetic pens use physical device pixels on screen; SVG widths use logical document pixels.
    # Account for Retina/high-DPI displays without changing text or non-cosmetic geometry.
    ratio = widget.devicePixelRatioF()
    if ratio != 1:
        parents = {child: parent for parent in root.iter() for child in parent}
        for element in root.iter():
            if element.get("vector-effect") == "non-scaling-stroke":
                owner = element
                while "stroke-width" not in owner.attrib and owner in parents:
                    owner = parents[owner]
                width = float(owner.get("stroke-width", "1"))
                element.set("stroke-width", format(width / ratio, ".17g"))
    colors = {curve.property("storedColor") for stored in widget.stored_plots
              for curve in stored.curves if curve.isVisible()}
    definitions = root.find(f"{{{namespace}}}defs")
    if definitions is None:
        definitions = ET.SubElement(root, f"{{{namespace}}}defs")
    live_colors = {curve.opts["pen"].color().name() for curve in (widget.linked, widget.left, widget.right)}
    if widget.cursor.line.isVisible():
        live_colors.add(widget.cursor.line.pen.color().name())
    repair_svg_clipping(root, definitions, widget, colors | live_colors)
    left, right = widget.plot.getViewBox().viewRange()[0]
    for index, color in enumerate(sorted(colors)):
        identifier = f"stored-edge-fade-{index}"
        gradient = ET.SubElement(definitions, f"{{{namespace}}}linearGradient", {
            "id": identifier, "gradientUnits": "userSpaceOnUse",
            "x1": format(left, ".17g"), "x2": format(right, ".17g"), "y1": "0", "y2": "0"})
        for offset, opacity in (("0", "0"), ("0.05", "1"), ("0.95", "1"), ("1", "0")):
            ET.SubElement(gradient, f"{{{namespace}}}stop", {
                "offset": offset, "stop-color": color, "stop-opacity": opacity})
        # Match only stored tints; axes, labels and the brighter live channel colors are left untouched.
        for element in root.iter():
            if element.get("stroke") == color:
                element.set("stroke", f"url(#{identifier})")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def save_plot(widget, destination, traces, view):
    """Export the visible plot or its numeric traces, replacing the destination only on success.

    Args:
        widget: SpectrumView to render, including labels, readout, margins and stored curves.
        destination: Resolved PNG, SVG or CSV output path.
        traces: Original per-bin channels for CSV export.
        view: Current representation identifier for CSV headings.
    """
    destination = Path(destination)
    # Stage output beside its target so a failed render cannot overwrite an existing valid export.
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".plot-", suffix=destination.suffix,
                                     delete=False) as output:
        temporary = Path(output.name)
    try:
        suffix = destination.suffix.lower()
        if suffix == ".csv":
            write_csv(temporary, traces, view)
        elif suffix == ".png":
            if not widget.grab().save(str(temporary), "PNG"):
                raise OSError("Could not encode PNG image")
        elif suffix == ".svg":
            # Render the graphics view directly: QWidget's backing-store export can embed a bitmap.
            # SVG 1.1 retains clipping and gradient brushes for the live/stored plot paths.
            from PySide6.QtCore import QRectF, Qt
            from PySide6.QtGui import QPainter
            from PySide6.QtSvg import QSvgGenerator
            generator = QSvgGenerator(QSvgGenerator.SvgVersion.Svg11)
            generator.setFileName(str(temporary))
            generator.setSize(widget.size())
            generator.setViewBox(widget.rect())
            # AxisItem uses cached QPictures, whose playback scales with paint-device DPI.
            # Match the widget's DPI so axes and fresh curve painting share the same coordinates.
            generator.setResolution(widget.logicalDpiX())
            generator.setTitle("VST3 Plugin Analyzer")
            painter = QPainter()
            if not painter.begin(generator):
                raise OSError("Could not create SVG image")
            try:
                painter.fillRect(widget.rect(), Qt.white)
                painter.setRenderHint(QPainter.Antialiasing)
                widget.plot.render(painter, QRectF(widget.plot.geometry()), widget.plot.viewport().rect())
            finally:
                painter.end()
            finalize_svg(temporary, widget)
        else:
            raise ValueError("Unsupported plot export format")
        if temporary.stat().st_size == 0:
            raise OSError("Plot export produced an empty file")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
