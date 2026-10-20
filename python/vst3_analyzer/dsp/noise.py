# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Stateful float64 white/pink noise and exact silence without extra dependencies."""
import numpy as np


class NoiseGenerator:
    """Produce continuous random input without restarting or normalizing individual audio blocks."""

    def __init__(self, kind, seed=None):
        """Select kind (white_noise, pink_noise or silence); seed optionally fixes the random sequence."""
        if kind not in ('white_noise', 'pink_noise', 'silence'):
            raise ValueError('Unknown noise generator')
        self.kind = kind  # Active generator, immutable for the lifetime of an acquisition stream.
        self.random = np.random.default_rng(seed)  # Private random sequence, independent of other application activity.
        # Paul Kellet's refined pinking filter, reproduced mathematically as block convolutions.
        # Source: https://www.firstpr.com.au/dsp/pink-noise/ (pk3, October 1999).
        self.poles = np.array([0.99886, 0.99332, 0.969, 0.8665, 0.55, -0.7616], dtype=np.float64)  # Six recursive poles.
        self.gains = np.array([0.0555179, 0.0750759, 0.153852, 0.3104856, 0.5329522, -0.016898], dtype=np.float64)  # Section input gains.
        self.states = np.zeros(6, dtype=np.float64)  # Last output of each recursive pinking section.
        self.previous_white = 0.0  # One-sample-delayed white contribution, retained across calls.
        self.powers = self.poles[:, None] ** np.arange(256)  # Bounded convolution kernels, reused for every block.

    def generate(self, frames):
        """Return frames continuous samples as float64, preserving RNG and pink-filter history."""
        if type(frames) is not int or frames < 0:
            raise ValueError('Expected a nonnegative integer frame count')
        if self.kind == 'silence':
            return np.zeros(frames, dtype=np.float64)
        white = self.random.uniform(-1.0, 1.0, frames)
        if self.kind == 'white_noise':
            return white
        # Bound convolution cost even for large host blocks; no Python loop runs once per sample.
        output = np.empty(frames, dtype=np.float64)
        for start in range(0, frames, 256):
            source = white[start:start + 256]
            count = source.size
            pink = source * 0.5362
            pink[0] += self.previous_white * 0.115926
            pink[1:] += source[:-1] * 0.115926
            for index, (pole, gain) in enumerate(zip(self.poles, self.gains)):
                # Closed-form one-pole recurrence includes the state from the preceding processing block.
                decay = self.powers[index, :count]
                section = np.convolve(source, gain * decay)[:count] + self.states[index] * pole * decay
                self.states[index] = section[-1]
                pink += section
            self.previous_white = float(source[-1])
            # Fixed headroom preserves natural noise dynamics; never clip or normalize each block's peak.
            output[start:start + count] = pink * 0.11
        return output
