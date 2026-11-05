# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Complex impulse-response analysis for magnitude, phase and sample group delay."""
from dataclasses import dataclass
from functools import cached_property
import numpy as np

DEFAULT_PHASE_THRESHOLD_DB = -160.0


@dataclass(frozen=True)
class FrequencyResponse:
    """One channel's response to a unit impulse, including DC and Nyquist."""

    frequencies_hz: np.ndarray  # Float64 FFT-bin frequencies.
    amplitudes_peak: np.ndarray  # Absolute transfer magnitude, unity for an unchanged impulse.
    spectrum: np.ndarray  # Complex128 transfer spectrum, retaining phase for alternate views.

    phase_mask: np.ndarray | None = None  # Optional hard validity mask, independent of the threshold.
    phase_threshold_db: float = DEFAULT_PHASE_THRESHOLD_DB
    delay_samples: np.ndarray | None = None  # Direct delay from the captured impulse, when available.
    phase_sources: tuple = ()  # Operand magnitudes let reference captures follow threshold changes.

    def phase_validity(self):
        """Return measured bins with sufficient magnitude for phase analysis."""
        floor = 10.0 ** (self.phase_threshold_db / 20.0)
        valid = np.isfinite(self.spectrum) & (self.amplitudes_peak >= floor)
        for magnitude in self.phase_sources:
            valid &= np.isfinite(magnitude) & (magnitude >= floor)
        if self.phase_mask is not None:
            valid &= self.phase_mask
        return valid

    def phase_interpolation_mask(self):
        """Mark only unknown bins bounded on both sides by measured phase."""
        return _bounded_gap_mask(self.phase_validity())

    @cached_property
    def _raw_delay(self):
        """Direct group delay in samples, corrected to the reported time origin."""
        if self.delay_samples is not None:
            return self.delay_samples
        # A spectrum-only response has no known finite, causal impulse record.
        # Retain phase differentiation for this case; real captures always supply direct delay.
        _, phase = self._phase_values()
        result = np.full(self.spectrum.shape, np.nan)
        indices = np.flatnonzero(np.isfinite(phase))
        if indices.size >= 2:
            first, last = indices[0], indices[-1]
            omega = np.pi * self.frequencies_hz[first:last + 1] / self.frequencies_hz[-1]
            result[first:last + 1] = -np.gradient(np.radians(phase[first:last + 1]), omega,
                                                  edge_order=2 if last-first > 1 else 1)
        return result

    def delay_interpolation_mask(self):
        return _bounded_gap_mask(self.phase_validity() & np.isfinite(self._raw_delay))

    def group_delay(self):
        """Keep direct values at supported bins and interpolate only bounded missing intervals."""
        result = np.full(self.spectrum.shape, np.nan)
        indices = np.flatnonzero(self.phase_validity() & np.isfinite(self._raw_delay))
        if indices.size:
            first, last = indices[0], indices[-1]
            result[first:last + 1] = np.interp(self.frequencies_hz[first:last + 1],
                                              self.frequencies_hz[indices], self._raw_delay[indices])
        return result

    def _phase_values(self):
        """Return wrapped/unwrapped degrees with bounded interpolation.

        Bins below the configured threshold are unknown. Interpolate bounded gaps
        along the shortest phase arc; never extrapolate past the outermost measured bins. Choose the
        unwrap branch at the stronger edge (low-frequency edge wins near ties),
        avoiding low-level stopband noise setting a high-pass filter's phase branch.
        """
        valid = self.phase_validity()
        indices = np.flatnonzero(valid)
        wrapped = np.full(self.spectrum.shape, np.nan)
        unwrapped = np.full(self.spectrum.shape, np.nan)
        if not indices.size:
            return wrapped, unwrapped
        measured = np.angle(self.spectrum[indices])
        continuous = np.unwrap(measured)
        first, last = indices[0], indices[-1]
        anchor = -1 if self.amplitudes_peak[last] > self.amplitudes_peak[first] * (1 + 1e-6) else 0
        continuous -= 2 * np.pi * np.rint((continuous[anchor] - measured[anchor]) / (2 * np.pi))
        region = np.interp(self.frequencies_hz[first:last + 1], self.frequencies_hz[indices], continuous)
        unwrapped[first:last + 1] = np.degrees(region)
        wrapped[first:last + 1] = np.degrees(np.angle(np.exp(1j * region)))
        wrapped[indices] = np.degrees(measured)
        return wrapped, unwrapped

    def phase_and_delay(self):
        """Return wrapped/unwrapped phase and direct group delay from a measured impulse."""
        return (*self._phase_values(), self.group_delay())


def _bounded_gap_mask(valid):
    indices = np.flatnonzero(valid)
    result = np.zeros(valid.shape, dtype=bool)
    if indices.size >= 2:
        result[indices[0]:indices[-1] + 1] = ~valid[indices[0]:indices[-1] + 1]
    return result


def _record_fft(signal, fft_size):
    """Evaluate the whole record on an FFT grid without truncating its latency prefix.

    Summing samples modulo fft_size evaluates exactly the same DFT frequencies
    even when the finite record is longer than the requested frequency grid.
    """
    blocks, remainder = divmod(signal.size, fft_size)
    folded = np.zeros(fft_size, dtype=np.float64)
    if blocks:
        folded += signal[:blocks * fft_size].reshape(blocks, fft_size).sum(axis=0)
    if remainder:
        folded[:remainder] += signal[blocks * fft_size:]
    return np.fft.rfft(folded)


def analyze_frequency_response(samples, sample_rate, phase_threshold_db=DEFAULT_PHASE_THRESHOLD_DB,
                               *, fft_size=None, latency_samples=0):
    """Analyze the complete raw impulse response, including pre-ringing.

    Args:
        samples: Finite output record beginning at the input impulse, including the latency prefix.
        sample_rate: Sample rate in Hz used to label FFT-bin frequencies.
        phase_threshold_db: Validity floor for phase/delay; does not change magnitude.
        fft_size: Even FFT grid size; defaults to the record length. All samples contribute.
        latency_samples: Reported latency to subtract from the time origin, without discarding data.
    """
    signal = np.asarray(samples, dtype=np.float64)
    if signal.ndim != 1 or signal.size < 2 or not np.isfinite(signal).all():
        raise ValueError("Expected a finite impulse response")
    if fft_size is None:
        fft_size = signal.size
    if type(fft_size) is not int or fft_size < 2 or fft_size % 2:
        raise ValueError("Expected an even FFT size of at least two samples")
    if type(latency_samples) is not int or not 0 <= latency_samples < signal.size:
        raise ValueError("Invalid impulse-response latency")
    if (isinstance(phase_threshold_db, bool) or not np.isfinite(phase_threshold_db) or
            not -200 <= phase_threshold_db <= -40):
        raise ValueError("Phase calculation threshold must be within [-200, -40] dB")
    if not np.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("Invalid sample rate")
    with np.errstate(over="ignore", invalid="ignore", divide="ignore", under="ignore"):
        raw_spectrum = _record_fft(signal, fft_size)
        magnitude = np.abs(raw_spectrum)
        # Centre the first moment before transforming, retaining actual sample times
        # rather than times modulo the FFT size (which would corrupt group delay).
        weighted = _record_fft((np.arange(signal.size, dtype=np.float64) - latency_samples) * signal,
                               fft_size)
        delay = np.real(weighted / raw_spectrum)
        omega = 2 * np.pi * np.fft.rfftfreq(fft_size)
        spectrum = raw_spectrum * np.exp(1j * omega * (latency_samples % fft_size))
    if not np.isfinite(magnitude).all():
        raise ValueError("FFT overflow: output amplitude is too large to analyze")
    delay[~np.isfinite(delay)] = np.nan
    return FrequencyResponse(np.fft.rfftfreq(fft_size, 1.0 / sample_rate), magnitude, spectrum,
                             phase_threshold_db=phase_threshold_db, delay_samples=delay)


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
    mask = np.ones(spectrum.shape, dtype=bool)
    for operand in (current, reference):
        if operand.phase_mask is not None:
            mask &= operand.phase_mask
    with np.errstate(invalid="ignore"):
        delay = current._raw_delay - reference._raw_delay
    return FrequencyResponse(current.frequencies_hz, np.abs(spectrum), spectrum, phase_mask=mask,
                             phase_threshold_db=max(current.phase_threshold_db, reference.phase_threshold_db),
                             delay_samples=delay,
                             phase_sources=(current.amplitudes_peak, reference.amplitudes_peak,
                                            *current.phase_sources, *reference.phase_sources))
