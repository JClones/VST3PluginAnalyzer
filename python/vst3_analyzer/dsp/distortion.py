# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Unwindowed coherent FFT and harmonic distortion in double precision."""
from dataclasses import dataclass
import math
import numpy as np
from .spectrum import analyze_spectrum


@dataclass(frozen=True)
class Harmonic:
    """One in-band harmonic, measured at its exact coherent FFT bin."""

    order: int  # Integer multiple of the fundamental, at least two.
    bin_index: int  # FFT bin index of the harmonic.
    frequency_hz: float  # Actual harmonic frequency in Hz.
    amplitude_peak: float  # Single-sided peak sinusoidal amplitude.


@dataclass(frozen=True)
class THDResult:
    """One channel's FFT/THD result; zero-output THD is undefined rather than zero."""

    frequencies_hz: np.ndarray  # One-sided float64 frequency axis, including DC/Nyquist.
    amplitudes_peak: np.ndarray  # One-sided float64 peak spectrum for plotting/export.
    fundamental_peak: float  # Measured fundamental peak amplitude.
    fundamental_rms: float  # Fundamental RMS amplitude.
    thd_ratio: float | None  # Root-sum-square harmonics divided by fundamental; None for no fundamental.
    thd_percent: float | None  # THD ratio multiplied by 100.
    thd_db: float | None  # 20 log10(THD ratio), -inf for exact zero, None for undefined.
    harmonics: tuple[Harmonic, ...]  # All integer harmonics strictly below Nyquist.


def analyze_thd(samples, sample_rate: float, fundamental_bin: int) -> THDResult:
    """Measure harmonics below Nyquist without a window, detrending or a noise-floor clamp."""
    # Reject invalid plugin output instead of producing misleading distortion figures.
    signal = np.asarray(samples, dtype=np.float64)
    if signal.ndim != 1 or signal.size < 2 or signal.size % 2 or not np.isfinite(signal).all():
        raise ValueError("Expected a finite, one-dimensional even-length audio record")
    size = signal.size
    if (not math.isfinite(sample_rate) or sample_rate <= 0 or type(fundamental_bin) is not int or
            not 1 <= fundamental_bin < size // 2):
        raise ValueError("Invalid sample rate or fundamental bin")

    # Share float64, single-sided peak normalization with the generator spectrum modes.
    result = analyze_spectrum(signal, sample_rate)
    amplitudes, frequencies = result.amplitudes_peak, result.frequencies_hz

    # Nyquist is excluded: its real-only bin cannot represent arbitrary sinusoidal phase.
    orders = range(2, (size // 2 - 1) // fundamental_bin + 1)
    harmonics = tuple(Harmonic(order, order * fundamental_bin,
                              float(frequencies[order * fundamental_bin]),
                              float(amplitudes[order * fundamental_bin])) for order in orders)
    fundamental = float(amplitudes[fundamental_bin])
    harmonic_peak = math.hypot(*(harmonic.amplitude_peak for harmonic in harmonics))
    ratio = harmonic_peak / fundamental if fundamental > 0 else None
    if ratio is not None and not math.isfinite(ratio):
        raise ValueError("THD ratio overflow: fundamental is too small relative to harmonics")
    db = None if ratio is None else (20.0 * math.log10(ratio) if ratio > 0 else -math.inf)
    return THDResult(frequencies, amplitudes, fundamental, fundamental / math.sqrt(2.0),
                     ratio, None if ratio is None else ratio * 100.0, db, harmonics)


@dataclass(frozen=True)
class CombinedDistortion:
    """Energy-combined distortion figures for one mono or stereo FFT record."""

    thd_db: float | None  # Harmonic RMS relative to fundamental RMS, in dB.
    thdn_db: float | None  # All non-DC residual RMS relative to fundamental RMS, in dB.
    klirr_percent: float | None  # Harmonic RMS relative to fundamental-plus-harmonic RMS, percent.


def combine_distortion(channels: tuple[THDResult, ...], fundamental_bin: int) -> CombinedDistortion:
    """Combine channel energies without summing waveforms or averaging channel ratios."""
    # Scale first to preserve float64 range; common RMS factors cancel in the ratios.
    scale = max((float(np.max(channel.amplitudes_peak)) for channel in channels), default=0.0)
    if scale == 0:
        return CombinedDistortion(None, None, None)
    fundamental = math.hypot(*(channel.fundamental_peak / scale for channel in channels))
    harmonic = math.hypot(*(item.amplitude_peak / scale for channel in channels for item in channel.harmonics))
    residuals = []
    for channel in channels:
        # Interior amplitudes are peak sinusoids; Nyquist has RMS equal to its amplitude.
        residual = channel.amplitudes_peak[1:].copy() / scale
        residual[fundamental_bin - 1] = 0
        residual[-1] *= math.sqrt(2.0)
        residuals.append(float(np.linalg.norm(residual)))
    noise_and_harmonics = math.hypot(*residuals)
    if fundamental == 0:
        return CombinedDistortion(None, None, None)
    # Log differences avoid overflowing very large distortion-to-fundamental ratios.
    thd_db = -math.inf if harmonic == 0 else 20 * (math.log10(harmonic) - math.log10(fundamental))
    thdn_db = (-math.inf if noise_and_harmonics == 0 else
               20 * (math.log10(noise_and_harmonics) - math.log10(fundamental)))
    return CombinedDistortion(thd_db, thdn_db, 100 * harmonic / math.hypot(fundamental, harmonic))
