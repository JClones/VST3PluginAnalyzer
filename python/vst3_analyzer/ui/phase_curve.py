# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Measured phase paths with separate interpolation estimates and phase-wrap connectors."""
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


def phase_paths(frequencies, values, interpolated, wrapped=False):
    """Separate measured phase from bounded estimates and optional wrap connectors."""
    if not np.any(interpolated):
        if wrapped:
            return wrapped_phase_paths(frequencies, values)
        return frequencies, values, np.array([]), np.array([])
    solid = values.copy()
    solid[interpolated] = np.nan
    # A bridge includes the reliable endpoints, but never joins unrelated gaps.
    segments = (interpolated[:-1] | interpolated[1:]) & np.isfinite(values[:-1]) & np.isfinite(values[1:])
    keep = np.r_[segments, False] | np.r_[False, segments]
    estimated = np.where(keep, values, np.nan)
    breaks = np.flatnonzero(keep[:-1] & keep[1:] & ~segments) + 1
    bridge_x = np.insert(frequencies, breaks, np.nan)
    bridge_y = np.insert(estimated, breaks, np.nan)
    if not wrapped:
        return frequencies, solid, bridge_x, bridge_y
    solid_x, solid_y, wrap_x, wrap_y = wrapped_phase_paths(frequencies, solid)
    bridge_x, bridge_y, bridge_wrap_x, bridge_wrap_y = wrapped_phase_paths(bridge_x, bridge_y)
    return (solid_x, solid_y, np.r_[wrap_x, np.nan, bridge_x, np.nan, bridge_wrap_x],
            np.r_[wrap_y, np.nan, bridge_y, np.nan, bridge_wrap_y])
