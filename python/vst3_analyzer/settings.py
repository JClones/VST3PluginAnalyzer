# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Validated user settings shared by command-line measurements and the future GUI."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class MeasurementSettings:
    """Acquisition and analysis settings; all numeric analysis uses float64."""

    sample_rate: float = 44100.0  # Host sample rate in Hz.
    fft_size: int = 8192  # Even number of samples in the unwindowed analysis record.
    block_size: int = 512  # Maximum native processing block size.
    frequency: float = 1000.0  # Requested sine frequency before rounding to a bin.
    precision: str = "auto"  # Native plugin mode: auto, float32 or float64.

    sine_level_dbfs: float = 0.0  # Requested peak sine level in dBFS; 0 dBFS gives amplitude one.

    def __post_init__(self):
        """Reject invalid settings before launching acquisition or allocating test vectors."""
        # Keep sizes bounded while allowing non-power-of-two even FFT lengths.
        if type(self.fft_size) is not int or self.fft_size < 32 or self.fft_size > 1048576 or self.fft_size % 2:
            raise ValueError("FFT size must be an even integer in [32, 1048576]")
        if type(self.block_size) is not int or not 1 <= self.block_size <= 65536:
            raise ValueError("Block size must be an integer in [1, 65536]")
        if (isinstance(self.sample_rate, bool) or not math.isfinite(self.sample_rate) or
                not 8000 <= self.sample_rate <= 768000):
            raise ValueError("Sample rate must be finite and within [8000, 768000]")
        if (isinstance(self.frequency, bool) or not math.isfinite(self.frequency) or
                not 0 < self.frequency < self.sample_rate / 2):
            raise ValueError("Frequency must be finite and between DC and Nyquist")
        if self.precision not in ("auto", "float32", "float64"):
            raise ValueError("Precision must be auto, float32 or float64")
        if (isinstance(self.sine_level_dbfs, bool) or not math.isfinite(self.sine_level_dbfs) or
                not -6000 <= self.sine_level_dbfs <= 6000):
            raise ValueError("Sine level must be finite and within [-6000, 6000] dBFS")
        selected = math.floor(self.frequency * self.fft_size / self.sample_rate + 0.5)
        if not 1 <= selected < self.fft_size // 2:
            raise ValueError("Nearest frequency bin is DC or Nyquist; change frequency or FFT size")

    @classmethod
    def load(cls, path: str | Path):
        """Load a JSON settings object; omitted fields retain the documented defaults."""
        # Constructor validation also rejects misspelled/unknown settings keys.
        with Path(path).open(encoding="utf-8") as stream:
            values = json.load(stream)
        if not isinstance(values, dict):
            raise ValueError("Settings file must contain a JSON object")
        return cls.from_dict(values)

    @classmethod
    def from_dict(cls, values: dict):
        """Restore settings while ignoring the removed legacy settling preference."""
        # Old desktop and CLI configurations remain usable without discarding other preferences.
        values = dict(values)
        values.pop("settle_seconds", None)
        try:
            return cls(**values)
        except TypeError as error:
            raise ValueError(f"Invalid settings: {error}") from error

    def save(self, path: str | Path):
        """Write a reusable human-readable settings file with explicit defaults."""
        # Store user requests; actual bin/frequency and processing mode belong in each result.
        Path(path).write_text(json.dumps(asdict(self), indent=2, allow_nan=False) + "\n", encoding="utf-8")
