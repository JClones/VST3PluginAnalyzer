# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Desktop editor for the shared validated measurement settings."""
from dataclasses import replace
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QGroupBox, QLabel, QMessageBox, QSpinBox, QWidget)


class FrequencySpinBox(QDoubleSpinBox):
    """Retain fractional frequency precision while hiding unnecessary decimal zeros."""

    def textFromValue(self, value):
        """Format frequency value with the widget locale and trim trailing fractional zeros."""
        # Reuse Qt formatting so localized decimal input continues to work normally.
        text = super().textFromValue(value)
        separator = self.locale().decimalPoint()
        if separator in text:
            text = text.rstrip("0").removesuffix(separator)
        return text


class SettingsDialog(QDialog):
    """Edit acquisition settings and requested harmonic-test frequency and level."""

    apply_requested = Signal(object)  # Validated settings to apply while keeping the dialog open.

    def __init__(self, settings, parent=None):
        """Populate compact numeric controls from the current immutable settings."""
        super().__init__(parent)
        self.setWindowTitle("Analyzer settings")
        self.active_sample_rate = settings.sample_rate  # Confirmed processing rate used for latency conversion.
        self.awaiting_apply = False  # Suppress stale latency while native reconfiguration is pending.
        self.original = settings  # Original settings retained until validation succeeds.
        self.result_settings = settings  # Validated settings returned after acceptance.
        self.rate = QComboBox()  # Editable sample rate in Hz.
        self.rate.setEditable(True)
        self.rate.addItems(["44100", "48000", "88200", "96000", "176400", "192000"])
        self.rate.setCurrentText(f"{settings.sample_rate:g}")
        self.size = QSpinBox()  # Even FFT length, independent of native processing block size.
        self.size.setRange(32, 1048576)
        self.size.setSingleStep(2)
        self.size.setValue(settings.fft_size)
        self.block = QSpinBox()  # Maximum configured native block size.
        self.block.setRange(1, 65536)
        self.block.setValue(settings.block_size)
        # Keep requested tone settings separate from the internally rounded FFT-bin frequency.
        self.frequency = FrequencySpinBox()  # Requested sine frequency in Hz, retained without bin rounding.
        self.frequency.setDecimals(6)
        self.frequency.setRange(0.000001, 384000)
        self.frequency.setValue(settings.frequency)
        self.level = QDoubleSpinBox()  # Peak sine level in dBFS, independent of measurement plot units.
        self.level.setDecimals(2)
        self.level.setRange(-6000, 6000)
        self.level.setValue(settings.sine_level_dbfs)
        harmonic = QGroupBox("Harmonic Response Test")
        harmonic_layout = QFormLayout(harmonic)
        harmonic_layout.addRow("Sine Frequency, Hz", self.frequency)
        harmonic_layout.addRow("Sine Level, dBFS", self.level)
        self.latency = QLabel()  # Current reported delay at the active plugin sample rate.
        self.set_latency(None)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply)
        buttons = self.buttons
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QFormLayout(self)
        layout.addRow("Sample rate (Hz)", self.rate)
        layout.addRow("FFT size", self.size)
        layout.addRow("Maximum block size", self.block)
        section_gap = QWidget()  # Extra separation between general settings and the harmonic test.
        section_gap.setFixedHeight(8)
        layout.addRow(section_gap)
        layout.addRow(harmonic)
        layout.addRow("Plugin latency", self.latency)
        layout.addRow(buttons)

    def set_latency(self, samples):
        """Display reported latency samples, or None when no measurement is available."""
        # Use the active rate, rather than an edited rate that has not yet been applied.
        if self.awaiting_apply:
            return
        if samples is None:
            self.latency.setText("Unavailable")
        else:
            milliseconds = samples * 1000.0 / self.active_sample_rate
            self.latency.setText(f"{samples} samples ({milliseconds:.3f} ms)")

    def _validate(self):
        """Read and validate edited controls, returning whether settings can be applied."""
        # Keep the dialog open with a useful error when a value cannot produce a valid tone.
        try:
            self.result_settings = replace(self.original, sample_rate=float(self.rate.currentText()),
                                           fft_size=self.size.value(), block_size=self.block.value(),
                                           precision="auto",
                                           frequency=self.frequency.value(),
                                           sine_level_dbfs=self.level.value())
        except ValueError as error:
            QMessageBox.warning(self, "Invalid settings", str(error))
            return False
        return True

    def apply(self):
        """Submit validated settings and leave the dialog open for further edits."""
        if not self._validate():
            return
        # The worker acknowledges the new rate and reported latency after configuration completes.
        self.awaiting_apply = True
        self.latency.setText("Updating…")
        self.buttons.button(QDialogButtonBox.Apply).setEnabled(False)
        self.apply_requested.emit(self.result_settings)

    def settings_applied(self, settings, latency):
        """Confirm active settings and refresh reported latency samples at their sample rate."""
        self.original = settings
        self.active_sample_rate = settings.sample_rate
        self.awaiting_apply = False
        self.buttons.button(QDialogButtonBox.Apply).setEnabled(True)
        self.set_latency(latency)

    def accept(self):
        """Validate edited controls and close the dialog so the caller can apply them."""
        if self._validate():
            super().accept()
