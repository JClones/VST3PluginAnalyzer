# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Versioned framing independent of process management and VST3 semantics."""

import json
import socket
import struct

VERSION = 2  # Shared wire-protocol version.
MAX_JSON = 1024 * 1024  # Maximum UTF-8 metadata bytes.
MAX_AUDIO = 16 * 1024 * 1024  # Maximum binary bytes in either direction.
HEADER = struct.Struct("!4sHHII")  # Magic, version, flags, JSON size, audio size.


class ProtocolError(ConnectionError):
    """A malformed or truncated peer message; the connection cannot be reused."""


def _unique_object(pairs):
    """Reject duplicate JSON keys rather than silently choosing one value."""
    # Ambiguous envelopes must never influence command or response interpretation.
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value):
    """Reject Python's nonstandard JSON NaN and infinity extensions."""
    # Audio may contain non-finite plugin output, but control metadata must be standard JSON.
    raise ProtocolError(f"Invalid JSON numeric constant: {value}")


def encode_frame(metadata: dict, payload: bytes = b"") -> bytes:
    """Encode one bounded message; audio bytes are not represented as JSON arrays."""
    # Validate all local data before the caller sends any bytes to a stream.
    if not isinstance(metadata, dict):
        raise ValueError("Metadata must be an object")
    data = json.dumps(metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if not 0 < len(data) <= MAX_JSON or len(payload) > MAX_AUDIO:
        raise ValueError("Frame size exceeds protocol limits")
    return HEADER.pack(b"V3PA", VERSION, 0, len(data), len(payload)) + data + payload


def read_exact(connection: socket.socket, size: int) -> bytes:
    """Read a precise byte count, rejecting EOF even after a partial read."""
    # Socket recv boundaries are unrelated to application message boundaries.
    parts = bytearray()
    while len(parts) < size:
        block = connection.recv(size - len(parts))
        if not block:
            raise ProtocolError("Host disconnected while receiving a frame")
        parts.extend(block)
    return bytes(parts)


def receive_frame(connection: socket.socket) -> tuple[dict, bytes]:
    """Receive and validate a complete frame before exposing its metadata."""
    # Check all allocation bounds from the fixed header first.
    magic, version, flags, json_size, audio_size = HEADER.unpack(read_exact(connection, HEADER.size))
    if magic != b"V3PA" or version != VERSION or flags:
        raise ProtocolError("Invalid frame magic, version or flags")
    if not 0 < json_size <= MAX_JSON or audio_size > MAX_AUDIO:
        raise ProtocolError("Frame size exceeds protocol limits")

    # Strict UTF-8 and JSON decoding prevent silent repair of malformed messages.
    try:
        metadata = json.loads(read_exact(connection, json_size).decode("utf-8"),
                              object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ProtocolError(f"Invalid JSON metadata: {error}") from error
    if not isinstance(metadata, dict):
        raise ProtocolError("Metadata must be a JSON object")
    return metadata, read_exact(connection, audio_size)
