# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Float64, 256-sample sinc reconstruction at arbitrary sample positions."""
import numpy as np


SINC_TAPS = 256  # Nearest sample contributions evaluated at each reconstructed point.


class SincInterpolator:
    """Reuse a vectorized interpolation kernel across channels on the same sample grid."""

    def __init__(self, positions):
        """Prepare positions in sample units relative to the measurement's time-zero origin.

        Args:
            positions: One-dimensional finite float64 reconstruction coordinates in sample units.
        """
        self.positions = np.asarray(positions, dtype=np.float64).copy()  # Query positions, independent of source length.
        if self.positions.ndim != 1 or not np.isfinite(self.positions).all():
            raise ValueError("Expected finite, one-dimensional sinc positions")
        # Millisecond-to-sample conversion can put exact sample instants a few ulps either side of an integer.
        rounded = np.rint(self.positions)
        self.positions = np.where(np.abs(self.positions - rounded) < 1e-10, rounded, self.positions)
        # Each fractional position uses the nearest 256 integer samples; no FFT or periodic extension is needed.
        centers = np.floor(self.positions)
        neighbors = np.arange(1 - SINC_TAPS // 2, 1 + SINC_TAPS // 2, dtype=np.float64)
        self.indices = centers[:, None] + neighbors  # Absolute integer sample coordinates.
        distance = self.positions[:, None] - self.indices
        self.weights = np.sinc(distance)  # Rectangularly truncated normalized sinc kernel.
        self.weights[(distance != 0) & (distance == np.rint(distance))] = 0  # Preserve original samples exactly.

    def evaluate(self, samples, origin_sample=0):
        """Reconstruct samples using zero extension at recorded boundaries, without wrapping.

        Args:
            samples: One-dimensional finite raw output samples, retained at float64 precision.
            origin_sample: Integer time-zero-relative coordinate of samples[0], negative for a latency prefix.
        """
        signal = np.asarray(samples, dtype=np.float64)
        if signal.ndim != 1 or signal.size == 0 or not np.isfinite(signal).all():
            raise ValueError("Expected finite, one-dimensional sinc samples")
        # Mask out-of-record taps before gathering; clipped indices never supply artificial edge samples.
        local = self.indices - origin_sample
        valid = (local >= 0) & (local < signal.size)
        indices = np.clip(local, 0, signal.size - 1).astype(np.intp)
        result = np.sum(np.where(valid, signal[indices], 0.0) * self.weights, axis=1, dtype=np.float64)
        # Display only the captured interval; missing data outside it must remain a gap.
        positions = self.positions - origin_sample
        result[(positions < 0) | (positions > signal.size - 1)] = np.nan
        return result
