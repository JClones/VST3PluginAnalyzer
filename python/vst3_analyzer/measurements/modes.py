# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Shared acquisition identifiers and labels for menus, persistence and plotting."""

MEASUREMENTS = {"frequency": "Frequency Response", "harmonic": "Harmonic Response",
                "impulse": "Impulse Response"}  # Stimulus-based measurements, in menu order.
GENERATORS = {"white_noise": "White Noise", "pink_noise": "Pink Noise",
              "silence": "Silence"}  # Continuous generator modes, in menu order.
SPECTRUM_MODES = ("harmonic", *GENERATORS)  # Modes using a rolling, peak-normalized magnitude FFT.
