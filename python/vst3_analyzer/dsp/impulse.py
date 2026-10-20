# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Time-domain impulse responses retaining the plugin's pre-zero latency interval."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class ImpulseResponse:
    """One raw output channel with time zero aligned to reported plugin latency."""

    times_ms: np.ndarray  # Float64 sample times; the latency prefix has negative coordinates.
    samples: np.ndarray  # Unnormalized float64 output, including startup and pre-ringing samples.


def analyze_impulse_response(samples, sample_rate, latency_samples):
    """Label a raw output record without discarding, transforming or normalizing samples.

    Args:
        samples: One finite output channel, including its reported latency prefix.
        sample_rate: Sampling frequency in Hz.
        latency_samples: Nonnegative integer delay placing the aligned impulse at time zero.
    """
    signal = np.asarray(samples, dtype=np.float64)
    if signal.ndim != 1 or signal.size == 0 or not np.isfinite(signal).all():
        raise ValueError("Expected a finite impulse response")
    if not np.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("Invalid sample rate")
    if type(latency_samples) is not int or not 0 <= latency_samples < signal.size:
        raise ValueError("Invalid impulse-response latency")
    # Preserve every recorded sample, including the sample exactly at time zero.
    times = (np.arange(signal.size, dtype=np.float64) - latency_samples) * (1000.0 / sample_rate)
    return ImpulseResponse(times, signal.copy())
