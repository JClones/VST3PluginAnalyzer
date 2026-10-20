# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""VST3 adapter using standard-library arrays; no SDK dependency in Python."""
from array import array
from pathlib import Path
from typing import Sequence
import sys

from ..ipc.client import HostClient, HostDisconnected
from ..ipc.protocol import MAX_AUDIO


class VST3DUT:
    """Expose a native VST3 service through the generic measurement-facing DUT API."""

    def __init__(self, client: HostClient):
        """Borrow the client's lifetime; its context manager owns the subprocess."""
        # Keep process management out of measurement algorithms.
        self.client = client  # Connected service client, owned by the application.

    def load(self, path: str | Path, class_id: str = "") -> dict:
        """Load an explicitly selected effect and return plugin/bus metadata."""
        # Normalize paths in Python so the child's working directory does not matter.
        return self.client.request("load_plugin", path=str(Path(path).resolve()), class_id=class_id)

    def configure(self, sample_rate: float = 44100, block_size: int = 512, precision: str = "auto",
                  processing_mode: str = "offline") -> dict:
        """Configure bounded offline processing in the native service."""
        # Native validation remains authoritative for plugin-specific capabilities.
        return self.client.request("configure", sample_rate=sample_rate, block_size=block_size,
                                   precision=precision, processing_mode=processing_mode)

    def parameters(self) -> list[dict]:
        """Get plugin parameter descriptors and normalized current values."""
        # Stable VST3 IDs are returned verbatim instead of converted to row indices.
        return self.client.request("get_parameters")["parameters"]

    def set_parameter(self, parameter_id: int, value: float) -> None:
        """Update a parameter in both controller and next processor block."""
        # The service checks ID existence, read-only flags and normalized range.
        self.client.request("set_parameter", parameter_id=parameter_id, value=value)

    def latency(self) -> int:
        """Query current reported latency rather than caching an initial value."""
        # Some effects change latency after parameter edits.
        return self.client.request("get_latency")["latency_samples"]

    def process(self, audio: Sequence[Sequence[float]]) -> list[array]:
        """Process one bounded channel-major chunk; larger tests use consecutive calls."""
        # Validate shape and payload bounds before allocating serialized audio.
        channels = len(audio)
        if channels not in (1, 2):
            raise ValueError("Expected mono or stereo channel-major audio")
        frames = len(audio[0])
        if any(len(channel) != frames for channel in audio):
            raise ValueError("All channels must have equal length")
        if channels * frames * 8 > MAX_AUDIO:
            raise ValueError("Chunk exceeds protocol audio limit; split into consecutive calls")

        # Arrays provide compact float64 storage without requiring NumPy for IPC tests.
        parts = []
        for channel in audio:
            samples = array("d", channel)
            if sys.byteorder != "little":
                samples.byteswap()
            parts.append(samples.tobytes())
        result, payload = self.client.exchange("process", payload=b"".join(parts), channels=channels,
                                               frames=frames, sample_format="f64le", layout="planar")

        # Validate returned dimensions before interpreting any plugin output bytes.
        if (type(result.get("channels")) is not int or result["channels"] != channels or
                type(result.get("frames")) is not int or result["frames"] != frames or
                result.get("sample_format") != "f64le" or result.get("layout") != "planar" or
                len(payload) != channels * frames * 8):
            self.client.close()
            raise HostDisconnected("Invalid audio response dimensions or format")
        output = []
        for index in range(channels):
            samples = array("d")
            samples.frombytes(payload[index * frames * 8:(index + 1) * frames * 8])
            if sys.byteorder != "little":
                samples.byteswap()
            output.append(samples)
        return output
