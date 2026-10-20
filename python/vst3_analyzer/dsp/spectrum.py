# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Unwindowed float64 magnitude spectra without harmonic or distortion measurements."""
from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class SpectrumResult:
    """A one-sided peak-amplitude spectrum, including DC and Nyquist."""

    frequencies_hz: np.ndarray  # Original float64 FFT-bin frequencies.
    amplitudes_peak: np.ndarray  # Float64 single-sided peak amplitudes, with DC/Nyquist scaled once.


def analyze_spectrum(samples, sample_rate):
    """Analyze samples as an unwindowed even-length record at sample_rate Hz."""
    signal = np.asarray(samples, dtype=np.float64)
    if signal.ndim != 1 or signal.size < 2 or signal.size % 2 or not np.isfinite(signal).all():
        raise ValueError('Expected a finite, one-dimensional even-length audio record')
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError('Invalid sample rate')
    # Float64 rfft gives complex128; double interior bins to account for negative frequencies.
    with np.errstate(over='ignore', invalid='ignore'):
        amplitudes = np.abs(np.fft.rfft(signal)) / signal.size
    amplitudes[1:-1] *= 2.0
    if not np.isfinite(amplitudes).all():
        raise ValueError('FFT overflow: output amplitude is too large to analyze')
    return SpectrumResult(np.fft.rfftfreq(signal.size, 1.0 / sample_rate), amplitudes)
