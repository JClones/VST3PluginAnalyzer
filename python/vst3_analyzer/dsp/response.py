# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Complex impulse-response analysis for magnitude, phase and sample group delay."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class FrequencyResponse:
    """One channel's response to a unit impulse, including DC and Nyquist."""

    frequencies_hz: np.ndarray  # Float64 FFT-bin frequencies.
    amplitudes_peak: np.ndarray  # Absolute transfer magnitude, unity for an unchanged impulse.
    spectrum: np.ndarray  # Complex128 transfer spectrum, retaining phase for alternate views.

    def phase_and_delay(self):
        """Return wrapped/unwrapped degrees and group delay in samples.

        Undefined zero-magnitude bins remain gaps; each contiguous valid region unwraps independently.
        """
        # Unwrap only defined phase regions, so a spectral zero cannot contaminate later bins.
        phase = np.angle(self.spectrum)
        valid = self.amplitudes_peak > 0
        unwrapped = np.full(phase.shape, np.nan)
        delay = np.full(phase.shape, np.nan)
        edges = np.flatnonzero(np.diff(np.r_[False, valid, False]))
        radians_per_bin = np.pi / (phase.size - 1)
        for start, stop in zip(edges[::2], edges[1::2]):
            region = np.unwrap(phase[start:stop])
            unwrapped[start:stop] = region
            # -d(phase)/d(angular frequency) yields samples when frequency is radians per sample.
            if region.size >= 2:
                delay[start:stop] = -np.gradient(region, radians_per_bin, edge_order=2 if region.size > 2 else 1)
        return np.where(valid, np.degrees(phase), np.nan), np.degrees(unwrapped), delay


def analyze_frequency_response(samples, sample_rate):
    """Transform one complete impulse period without windowing or sine normalization.

    Args:
        samples: Finite, even-length time-domain output record for a unit-impulse input.
        sample_rate: Sample rate in Hz used to label FFT-bin frequencies.
    """
    # A unit impulse has unity spectrum at every bin; no division by record length is needed.
    signal = np.asarray(samples, dtype=np.float64)
    if signal.ndim != 1 or signal.size < 2 or signal.size % 2 or not np.isfinite(signal).all():
        raise ValueError("Expected a finite, even-length impulse response")
    if not np.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("Invalid sample rate")
    with np.errstate(over="ignore", invalid="ignore"):
        spectrum = np.fft.rfft(signal)
        magnitude = np.abs(spectrum)
    if not np.isfinite(magnitude).all():
        raise ValueError("FFT overflow: output amplitude is too large to analyze")
    return FrequencyResponse(np.fft.rfftfreq(signal.size, 1.0 / sample_rate), magnitude, spectrum)


def relative_response(current, reference):
    """Return the complex current/reference response for matching FFT grids.

    Args:
        current: Latest absolute frequency response for one channel.
        reference: Immutable absolute reference response for that channel.
    """
    if not np.array_equal(current.frequencies_hz, reference.frequencies_hz):
        raise ValueError("Reference and current frequency grids differ")
    # A zero reference has no defined ratio; leave a visible gap rather than inventing a denominator.
    spectrum = np.full(current.spectrum.shape, complex(np.nan, np.nan), dtype=np.complex128)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        np.divide(current.spectrum, reference.spectrum, out=spectrum, where=reference.amplitudes_peak > 0)
    spectrum[~np.isfinite(spectrum)] = complex(np.nan, np.nan)
    return FrequencyResponse(current.frequencies_hz, np.abs(spectrum), spectrum)
