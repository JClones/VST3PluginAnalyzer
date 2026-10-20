# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Double-precision, FFT-coherent signal generation without windowing."""
import math
import numpy as np


def nearest_bin(frequency: float, sample_rate: float, fft_size: int) -> int:
    """Round to the nearest positive FFT bin, choosing the upper bin on an exact tie."""
    # Reject DC/Nyquist instead of silently moving the requested test to a different region.
    if (not math.isfinite(frequency) or not math.isfinite(sample_rate) or sample_rate <= 0 or
            type(fft_size) is not int or fft_size < 2 or fft_size % 2):
        raise ValueError("Invalid frequency, rate or FFT size")
    selected = math.floor(frequency * fft_size / sample_rate + 0.5)
    if not 1 <= selected < fft_size // 2:
        raise ValueError("Nearest frequency bin must be strictly between DC and Nyquist")
    return selected


def coherent_sine(bin_index: int, fft_size: int, frames: int | None = None, *, offset: int = 0) -> np.ndarray:
    """Generate a peak-amplitude 1.0 float64 sine; offset preserves phase across chunks."""
    # Wrap integer phase before converting to radians to make repeated FFT periods identical.
    if type(fft_size) is not int or fft_size < 2 or not 1 <= bin_index < fft_size / 2:
        raise ValueError("Invalid FFT size or fundamental bin")
    frames = fft_size if frames is None else frames
    if type(frames) is not int or frames < 0 or type(offset) is not int or offset < 0:
        raise ValueError("Frames and offset must be nonnegative integers")
    phase = ((np.arange(frames, dtype=np.int64) + offset % fft_size) % fft_size * bin_index) % fft_size
    return np.sin(phase.astype(np.float64) * (2.0 * np.pi / fft_size))
