# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Serialized request/response client and private host subprocess lifecycle."""

from pathlib import Path
import socket
import math
import subprocess
import tempfile
import threading
import time
import logging
import os

from .protocol import ProtocolError, VERSION, encode_frame, receive_frame

LOGGER = logging.getLogger(__name__)  # Host failures remain available through standard Python logging.
LONG_RUNNING_COMMANDS = frozenset({"hello", "list_classes", "load_plugin", "load_preset", "configure",
                                     "has_editor", "restore_editor", "show_editor", "performance"})  # Explicit long operations.
TRACED_COMMANDS = LONG_RUNNING_COMMANDS | {"save_preset", "unload_plugin", "shutdown"}  # Omit audio/polling traffic.


class HostError(RuntimeError):
    """A structured command failure; the socket remains usable."""

    def __init__(self, code: str, message: str):
        """Retain the machine-readable service error code and human-readable message."""
        # Callers can distinguish request errors from host execution errors.
        super().__init__(message)
        self.code = code  # Stable protocol error category.


class HostDisconnected(ConnectionError):
    """Transport failure or timeout; restart and reload before continuing."""


class HostClient:
    """Own one service process and serialize calls; run blocking calls outside a future GUI thread."""

    def __init__(self, executable: str | Path, *, timeout: float = 10.0, transport: str = "socketpair",
                 initialization_timeout: float | None = None):
        """Configure the executable, normal timeout, transport and optional longer initialization_timeout."""
        # Resolve early so changing the working directory does not alter service selection.
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be positive")
        if transport not in ("socketpair", "named"):
            raise ValueError("Transport must be socketpair or named")
        if initialization_timeout is not None and (not math.isfinite(initialization_timeout) or initialization_timeout <= 0):
            raise ValueError("Initialization timeout must be positive")
        self.transport = transport  # Connected inherited pair by default; named path is optional.
        self.executable = Path(executable).resolve()  # Native host binary to launch.
        self.timeout = timeout  # Blocking operation/startup timeout in seconds.
        self.initialization_timeout = initialization_timeout  # Optional budget for lifecycle/editor operations and explicit tests.
        self._process = None  # Owned subprocess, or None while stopped.
        self._socket = None  # Connected Unix-domain socket, or None while stopped.
        self._directory = None  # Private short-path TemporaryDirectory owner.
        self._log = None  # Temporary binary log file; avoids pipe-buffer deadlocks.
        self._next_id = 1  # Monotonically increasing uint32 request identifier.
        self._lock = threading.RLock()  # Prevent interleaved frames or concurrent close.
        self.capabilities = {}  # Negotiated hello result, empty while stopped.

    def __enter__(self):
        """Start a host when entering a with statement."""
        # Context management ensures plugin and temporary files are released on exceptions.
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Shut down the service when leaving a with statement."""
        # Do not suppress the caller's original exception.
        self.close()

    def start(self):
        """Launch, wait for socket readiness and complete the protocol handshake."""
        with self._lock:
            # Reject accidental replacement of a running instance; restart is explicit.
            if self._process is not None:
                raise RuntimeError("Host already started; use restart()")
            self._log = tempfile.TemporaryFile()
            LOGGER.info("Starting host: executable=%s transport=%s", self.executable, self.transport)
            try:
                if self.transport == "socketpair":
                    # Pass only the child's AF_UNIX endpoint; no filesystem socket or accept race.
                    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
                    self._socket = parent
                    parent.settimeout(self.timeout)
                    try:
                        self._process = subprocess.Popen(
                            [str(self.executable), "--socket-fd", str(child.fileno())],
                            pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL,
                            stdout=self._log, stderr=self._log)
                    finally:
                        child.close()
                else:
                    # Standalone service mode creates a short path in a private directory.
                    self._directory = tempfile.TemporaryDirectory(prefix="v3pa-", dir="/tmp")
                    path = str(Path(self._directory.name) / "host.sock")
                    self._process = subprocess.Popen([str(self.executable), "--socket", path],
                                                     stdin=subprocess.DEVNULL, stdout=self._log,
                                                     stderr=self._log)
                    deadline = time.monotonic() + (self.initialization_timeout or self.timeout)
                    while True:
                        if self._process.poll() is not None:
                            raise HostDisconnected(f"Host exited during startup: {self._diagnostic()}")
                        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        connection.settimeout(self.timeout)
                        try:
                            connection.connect(path)
                            self._socket = connection
                            break
                        except (FileNotFoundError, ConnectionRefusedError):
                            connection.close()
                            if time.monotonic() >= deadline:
                                raise HostDisconnected("Timed out waiting for host socket")
                            time.sleep(0.02)
                        except BaseException:
                            connection.close()
                            raise
                self._next_id = 1
                self.capabilities = self.request("hello", protocol_version=VERSION)
                if self.capabilities.get("protocol_version") != VERSION:
                    raise ProtocolError("Host handshake returned an incompatible version")
            except BaseException:
                # Failed startup never leaves a subprocess or temporary directory behind.
                LOGGER.exception("Host startup failed: executable=%s", self.executable)
                self._dispose()
                raise

    def _diagnostic(self):
        """Return a bounded tail of the service log after a failure."""
        # Read a bounded tail without changing the file offset shared with the child's stdout/stderr.
        if self._log is None:
            return ""
        offset = max(0, os.fstat(self._log.fileno()).st_size - 4096)
        return os.pread(self._log.fileno(), 4096, offset).decode("utf-8", errors="replace").strip()

    def exchange(self, command: str, *, payload: bytes = b"", **fields) -> tuple[dict, bytes]:
        """Exchange one command; transport failures invalidate and terminate the host."""
        with self._lock:
            # Encode before sending so invalid local values do not poison an otherwise healthy socket.
            if self._socket is None:
                raise HostDisconnected("Host is stopped; start or restart it first")
            if "id" in fields or "cmd" in fields:
                raise ValueError("id and cmd are reserved envelope fields")
            request_id = self._next_id
            encoded = encode_frame({"id": request_id, "cmd": command, **fields}, payload)
            self._next_id = 1 if request_id == 0xFFFFFFFF else request_id + 1
            connection = self._socket
            normal_timeout = connection.gettimeout()
            if command in LONG_RUNNING_COMMANDS and self.initialization_timeout is not None and normal_timeout is not None:
                connection.settimeout(max(normal_timeout, self.initialization_timeout))
            operation_timeout = connection.gettimeout()
            started = time.monotonic()
            pid = self._process.pid if self._process is not None else None
            if command in TRACED_COMMANDS:
                LOGGER.info("Host request started: command=%s id=%d pid=%s timeout=%s path=%s",
                            command, request_id, pid, operation_timeout, fields.get("path", ""))
            try:
                self._socket.sendall(encoded)
                response, audio = receive_frame(self._socket)
                if type(response.get("id")) is not int or response["id"] != request_id:
                    raise ProtocolError("Response ID does not match request")
                if type(response.get("ok")) is not bool:
                    raise ProtocolError("Response is missing a boolean status")
                if not response["ok"]:
                    error = response.get("error")
                    if audio or not isinstance(error, dict) or not all(
                        isinstance(error.get(key), str) for key in ("code", "message")
                    ):
                        raise ProtocolError("Malformed error response")
                    LOGGER.warning("Host request rejected: command=%s id=%d code=%s message=%s",
                                   command, request_id, error["code"], error["message"])
                    raise HostError(error["code"], error["message"])
                if not isinstance(response.get("result"), dict):
                    raise ProtocolError("Response result must be an object")
                if command in TRACED_COMMANDS:
                    LOGGER.info("Host request completed: command=%s id=%d elapsed=%.3fs", command, request_id, time.monotonic() - started)
                return response["result"], audio
            except (OSError, ProtocolError) as error:
                # Never reuse a stream after a partial transfer or timeout.
                diagnostic = self._diagnostic()
                message = (f"Host command '{command}' (request {request_id}, PID {pid}) failed after "
                           f"{time.monotonic() - started:.2f} s (socket timeout {operation_timeout} s): {error}")
                if diagnostic:
                    message += f"\nNative host log tail:\n{diagnostic}"
                LOGGER.error("%s", message)
                self._dispose()
                raise HostDisconnected(message) from error
            finally:
                # A slow initialization request must never relax the next audio/polling/shutdown timeout.
                if self._socket is connection:
                    connection.settimeout(normal_timeout)

    def request(self, command: str, **fields) -> dict:
        """Send a control-only request, rejecting unexpected binary response data."""
        with self._lock:
            # Only process is allowed to return audio payloads.
            result, payload = self.exchange(command, **fields)
            if payload:
                self._dispose()
                raise HostDisconnected("Unexpected audio in control response")
            return result

    def restart(self):
        """Replace a stopped, crashed or running host with a fresh unloaded instance."""
        with self._lock:
            # Plugin reload/configuration is intentionally explicit after restart.
            self.close()
            self.start()

    def close(self):
        """Return true after graceful shutdown; terminate/kill an unresponsive service and return false."""
        with self._lock:
            clean = False
            # A plugin may hang; cleanup cannot depend on it acknowledging shutdown.
            if self._socket is not None and self._process is not None and self._process.poll() is None:
                try:
                    self._socket.settimeout(min(self.timeout, 0.5))
                    self.request("shutdown")
                    clean = self._process.wait(timeout=0.5) == 0
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    pass
            self._dispose()
            return clean

    def _dispose(self):
        """Release owned resources without issuing protocol commands."""
        # Closing the socket first also unblocks a healthy server waiting for another request.
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=1.0)
            self._process = None
        if self._log is not None:
            self._log.close()
            self._log = None
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None
        self.capabilities = {}
