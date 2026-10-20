# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Display-only paths for wrapped phase, with faint straight boundary legs and wrap connections."""
import numpy as np


def wrapped_phase_paths(frequencies, values):
    """Split solid phase at wraps and draw all imaginary segments as straight connectors.

    Args:
        frequencies: Positive FFT-bin frequencies in ascending order.
        values: Corresponding wrapped phase in degrees, with NaNs for undefined bins.

    Returns:
        Solid-path frequencies/phase and connector frequencies/phase, separated by NaNs.
    """
    # Detect only finite jumps; undefined response bins must remain disconnected.
    delta = np.diff(values)
    jumps = np.flatnonzero(np.isfinite(values[:-1]) & np.isfinite(values[1:]) & (np.abs(delta) > 180))
    if not jumps.size:
        return frequencies, values, np.array([]), np.array([])

    # Interpolate the short unwrapped phase segment to its exact +/-180-degree crossing.
    boundary = np.where(delta[jumps] > 0, -180.0, 180.0)
    short_delta = delta[jumps] - np.sign(delta[jumps]) * 360
    fraction = np.divide(boundary - values[jumps], short_delta,
                         out=np.full(jumps.size, 0.5), where=short_delta != 0)
    fraction = np.clip(fraction, 0, 1)
    left = np.log10(frequencies[jumps])
    right = np.log10(frequencies[jumps + 1])
    crossing = left + (right - left) * fraction
    crossing_hz = 10 ** crossing

    # Keep measured samples on the solid curve, but break its segment across every wrap.
    separators = np.full(jumps.size, np.nan)
    solid_x = np.insert(frequencies, jumps + 1, separators)
    solid_y = np.insert(values, jumps + 1, separators)

    # All interpolated parts are imaginary: point-to-boundary, opposite-boundary return, and final leg.
    # Straight segments share the exact crossing frequency and stay within the measured bin interval.
    connector_x = np.column_stack((frequencies[jumps], crossing_hz, crossing_hz,
                                   frequencies[jumps + 1], separators)).ravel()
    connector_y = np.column_stack((values[jumps], boundary, -boundary,
                                   values[jumps + 1], separators)).ravel()
    return solid_x, solid_y, connector_x, connector_y
