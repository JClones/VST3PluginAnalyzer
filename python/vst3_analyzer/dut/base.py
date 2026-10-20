# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Minimal measurement-facing device interface, independent of VST3 and sockets."""
from typing import Protocol, Sequence


class DUT(Protocol):
    """An audio device with explicit configuration and channel-major processing."""

    def configure(self, sample_rate: float = 44100, block_size: int = 512, precision: str = "auto",
                  processing_mode: str = "offline") -> dict:
        """Configure the device and return its supported channel layout."""
        ...

    def process(self, audio: Sequence[Sequence[float]]) -> list:
        """Process consecutive audio while preserving device state between calls."""
        ...

    def parameters(self) -> list[dict]:
        """Return stable parameter descriptors and current normalized values."""
        ...

    def set_parameter(self, parameter_id: int, value: float) -> None:
        """Set a normalized parameter value for the next processed block."""
        ...

    def latency(self) -> int:
        """Return reported device latency in samples."""
        ...
