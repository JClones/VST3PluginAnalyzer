# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Read-only performance-test log with an OK button enabled after the native tests finish."""
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout


class PerformanceDialog(QDialog):
    """Keep the GUI responsive while the worker runs native timing commands."""

    def __init__(self, plugin_name, parent=None):
        """Create a log window for plugin_name, parented to the analyzer's main window."""
        super().__init__(parent)
        self.setWindowTitle(f'{plugin_name} — Performance' if plugin_name else 'Performance')
        self.resize(540, 420)
        self.log = QPlainTextEdit(self)  # Selectable, copyable text log; no editing or rich-text formatting.
        self.log.setReadOnly(True)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.log.setPlaceholderText('Running performance tests…')
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok, self)  # Completion closes the test and resumes acquisition.
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(self.log)
        layout.addWidget(self.buttons)

    def update_result(self, update):
        """Append an update containing a test result, completion flag or error from the worker."""
        if 'processing_ms' in update:
            if update.get('label') == 'random':
                self.log.appendPlainText('')
            # Fixed decimal milliseconds retain the native high-resolution timing without including IPC elapsed time.
            self.log.appendPlainText(f"Test with {update['samples']} {update['label']} samples:\n"
                                     f"Time: {update['processing_ms']:.6f} ms")
        if 'error' in update:
            self.log.appendPlainText(f"Error: {update['error']}")
        if update.get('complete') or 'error' in update:
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
