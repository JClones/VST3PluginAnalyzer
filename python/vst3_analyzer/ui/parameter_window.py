# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Independent scrollable parameter editor with normalized controls and VST3 value text."""
import math
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHeaderView, QLineEdit,
                               QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)


class ParameterSlider(QSlider):
    """Map a horizontal slider to normalized values, with gestures restoring the SDK default."""

    normalized_changed = Signal(float)  # Normalized parameter edit requested by the user.

    def __init__(self, parameter, parent=None):
        """Configure the slider from parameter metadata and attach it to parent."""
        super().__init__(Qt.Horizontal, parent)
        self.default_value = parameter["default_value"]  # SDK-declared normalized reset value.
        self.setRange(0, parameter["steps"] if parameter["steps"] > 0 else 1000000)
        self.set_normalized_value(parameter["value"], notify=False)
        self.valueChanged.connect(self._moved)
        self.setToolTip("Double-click or Alt-click to reset to default")

    def _moved(self, value):
        """Translate integer slider value into a normalized edit."""
        self.normalized_changed.emit(value / self.maximum())

    def normalized_value(self):
        """Return the current slider position in the inclusive normalized range."""
        return self.value() / self.maximum()

    def set_normalized_value(self, value, notify=True):
        """Set normalized value and optionally notify the host, preserving exact requested values."""
        with QSignalBlocker(self):
            self.setValue(int(math.floor(value * self.maximum() + 0.5)))
        if notify:
            self.normalized_changed.emit(value)

    def mouseDoubleClickEvent(self, event):
        """Restore the declared default on a left-button double-click event."""
        if event.button() == Qt.LeftButton:
            self.set_normalized_value(self.default_value)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        """Handle Alt-left-click event as a precise default reset; otherwise use normal dragging."""
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.AltModifier:
            self.set_normalized_value(self.default_value)
            event.accept()
        else:
            super().mousePressEvent(event)


class ParameterToggle(QCheckBox):
    """Represent SDK stepCount=1 parameters as normalized on/off controls."""

    normalized_changed = Signal(float)  # Normalized off/on edit requested by the user.

    def __init__(self, parameter, parent=None):
        """Initialize checkbox state from parameter metadata and attach it to parent."""
        super().__init__(parent)
        self.set_normalized_value(parameter["value"], notify=False)
        self.toggled.connect(lambda checked: self.normalized_changed.emit(float(checked)))

    def normalized_value(self):
        """Return one for checked and zero for unchecked."""
        return float(self.isChecked())

    def set_normalized_value(self, value, notify=True):
        """Set normalized off/on value and optionally emit the requested host edit."""
        with QSignalBlocker(self):
            self.setChecked(value >= 0.5)
        if notify:
            self.normalized_changed.emit(self.normalized_value())


class ParameterValueEdit(QLineEdit):
    """Select the value on entry, while allowing normal selection once focused."""

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._select_on_release = event.reason() == Qt.MouseFocusReason
        if not self.isReadOnly():
            self.selectAll()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and getattr(self, "_select_on_release", False):
            self._select_on_release = False
            if not self.isReadOnly() and not self.hasSelectedText():
                self.selectAll()


def _parameter_value_text(parameter):
    """Append SDK units once, keeping the plugin's own formatted value intact."""
    text = parameter["display"]
    units = parameter.get("units", "").strip()
    return f"{text} {units}" if units and not text.rstrip().endswith(units) else text


class ParameterWindow(QWidget):
    """Own a separate, resizable parameter window whose table provides scrolling."""

    normalized_requested = Signal(int, float)  # Stable SDK ID and requested normalized value.
    text_requested = Signal(int, str)  # Stable SDK ID and plugin-interpreted value text.
    visibility_changed = Signal(bool)  # Synchronize the Window menu checkbox on show/hide/close.

    def __init__(self, parent=None):
        """Create the independent parameter window with its owning main window parent."""
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Parameter List")
        self.resize(720, 500)
        self.rows = {}  # Stable parameter ID to table row; never exposed as a visible column.
        self.metadata = {}  # Last authoritative values, used to restore rejected text edits.
        self.editors = {}  # Stable parameter ID to editable formatted value.
        self.table = QTableWidget(0, 3, self)  # Scrollable name, normalized control and logical value columns.
        self.table.setHorizontalHeaderLabels(["Parameter", "Value", "Plugin value"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        for column in (0, 1):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.setColumnWidth(2, 180)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)

    def set_parameters(self, parameters):
        """Build controls from the plugin's parameter descriptors in parameters."""
        self.rows.clear()
        self.metadata.clear()
        self.editors.clear()
        self.table.setRowCount(0)
        self.table.setRowCount(len(parameters))
        for row, parameter in enumerate(parameters):
            identifier = parameter["id"]
            self.rows[identifier] = row
            self.metadata[identifier] = parameter
            self.table.setItem(row, 0, QTableWidgetItem(parameter["title"]))
            control = ParameterToggle(parameter) if parameter["steps"] == 1 else ParameterSlider(parameter)
            control.setEnabled(not (parameter["flags"] & 2))
            control.normalized_changed.connect(
                lambda value, parameter_id=identifier: self.normalized_requested.emit(parameter_id, value))
            self.table.setCellWidget(row, 1, control)
            editor = ParameterValueEdit(_parameter_value_text(parameter))
            editor.setReadOnly(bool(parameter["flags"] & 2))
            editor.setToolTip(parameter["units"])
            editor.editingFinished.connect(lambda parameter_id=identifier: self._submit_text(parameter_id))
            self.editors[identifier] = editor
            self.table.setCellWidget(row, 2, editor)
        self.table.resizeRowsToContents()

    def _submit_text(self, identifier):
        """Submit modified logical value text for stable parameter identifier."""
        editor = self.editors[identifier]
        if editor.isModified():
            editor.setModified(False)
            text = editor.text()
            parameter = self.metadata[identifier]
            units = parameter.get("units", "").strip()
            # Only remove the suffix we added. Plugins control their own text grammar,
            # and VST3 does not require parsers to accept the separate SDK units field.
            if units and not parameter["display"].rstrip().endswith(units):
                suffix = " " + units
                if text.rstrip().endswith(suffix):
                    text = text.rstrip()[:-len(suffix)].rstrip()
            self.text_requested.emit(identifier, text)

    def update_parameters(self, parameters, force_text_id=None):
        """Refresh parameters without overwriting active typing or dragging; force_text_id confirms an edit."""
        for parameter in parameters:
            identifier = parameter["id"]
            if identifier not in self.rows:
                continue
            self.metadata[identifier] = parameter
            row = self.rows[identifier]
            control = self.table.cellWidget(row, 1)
            if not isinstance(control, QSlider) or not control.isSliderDown():
                control.set_normalized_value(parameter["value"], notify=False)
            editor = self.editors[identifier]
            editor.setToolTip(parameter["units"])
            # Polling must not reset the cursor/selection, even before the first keystroke.
            if identifier == force_text_id or (not editor.isModified() and not editor.hasFocus()):
                text = _parameter_value_text(parameter)
                if editor.text() != text:
                    editor.setText(text)
                editor.setModified(False)

    def restore_text(self, identifier):
        """Restore the last authoritative value after a rejected text edit for identifier."""
        if identifier in self.rows:
            editor = self.editors[identifier]
            editor.setText(_parameter_value_text(self.metadata[identifier]))
            editor.setModified(False)

    def showEvent(self, event):
        """Notify the menu when this window becomes visible through show event."""
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event):
        """Notify the menu when close or hide event conceals this window."""
        super().hideEvent(event)
        self.visibility_changed.emit(False)
