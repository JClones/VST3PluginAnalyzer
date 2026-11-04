# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Continuous impulse capture retaining latency prefixes and optional recovery output."""
import numpy as np
from ..dsp.impulse import analyze_impulse_response


class ImpulseCapture:
    """Retain raw output from each input impulse through its latency and requested record length."""

    def __init__(self, channels, record_size, sample_rate, *, complete_period=False):
        """Create history for channels outputs, record_size post-zero samples and sample_rate Hz."""
        self.record_size = record_size  # Number of samples retained after the reported latency.
        self.complete_period = complete_period  # Frequency analysis retains the silent recovery output too.
        self.period_size = record_size if complete_period else 2 * record_size  # Updated with latency on append.
        self.sample_rate = sample_rate  # Frequency used to express sample times in milliseconds.
        self.pending = np.empty((channels, 0), dtype=np.float64)  # Raw output from the next impulse origin.
        self.skip_remaining = 0  # Unneeded output until the next input impulse's raw latency prefix begins.

    def remaining(self, latency):
        """Return samples still needed for the next record using reported latency samples."""
        if self.skip_remaining:
            return self.skip_remaining
        return max(1, self.record_size + latency - self.pending.shape[1])

    def append(self, output, latency):
        """Retain output channels and return a complete response using reported latency samples.

        Args:
            output: New float64 channel-by-sample block in uninterrupted processing order.
            latency: Current reported plugin delay in samples, including first-block updates.
        """
        # Process recovery samples normally, retaining only any part belonging to the next capture's prefix.
        skipped = min(self.skip_remaining, output.shape[1])
        self.skip_remaining -= skipped
        output = output[:, skipped:]
        if not output.size:
            return None
        # Retain the entire prefix, even if latency changes during processing.
        self.pending = np.concatenate((self.pending, output), axis=1)
        length = self.record_size + latency
        if self.complete_period:
            self.period_size = length
        if self.pending.shape[1] < length:
            return None
        record = self.pending[:, :length]
        linked = record.shape[0] == 1 or np.array_equal(record[0], record[1])
        responses = tuple(analyze_impulse_response(channel, self.sample_rate, latency) for channel in record)
        # Long latency prefixes can overlap captures; otherwise ignore output until the next impulse origin.
        self.skip_remaining = max(0, self.period_size - self.pending.shape[1])
        self.pending = self.pending[:, self.period_size:].copy()
        return responses, linked
