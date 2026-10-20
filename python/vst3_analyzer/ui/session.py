# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Atomic desktop configuration and clean-exit gating for automatic plugin restoration."""
import json
import os
import shutil
from pathlib import Path
import tempfile
import uuid
from PySide6.QtCore import QLockFile, QStandardPaths

from .branding import APPLICATION_NAME


class SessionStore:
    """Keep the first session persistent and concurrent sessions isolated."""

    def __init__(self, directory=None):
        """Select persistent storage or temporary storage when another instance owns it."""
        # Explicit directories let tests isolate their state from the user's application.
        self.directory = Path(directory) if directory is not None else Path.home() / ".config" / APPLICATION_NAME  # Shared Mac/Linux config root.
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "config.json"  # Human-readable application configuration.
        self.lock = QLockFile(str(self.directory / "session.lock"))  # Cross-process config ownership.
        self.lock.setStaleLockTime(0)  # A live long-running instance must never age out of its lock.
        self._temporary_directory = None  # Additional instances never write shared session files.
        if not self.lock.tryLock(0):
            # Read only preferences from the atomic shared config. A concurrent instance
            # starts without a plugin and never relies on another process's preset files.
            try:
                shared = json.loads(self.path.read_text(encoding="utf-8"))
                preferences = shared.get("ui", {}) if isinstance(shared, dict) and shared.get("version") == 1 else {}
            except (OSError, ValueError):
                preferences = {}
            self._temporary_directory = tempfile.TemporaryDirectory(prefix="v3pa-session-")
            self.directory = Path(self._temporary_directory.name)
            self.path = self.directory / "config.json"
            self.lock = QLockFile(str(self.directory / "session.lock"))
            self.lock.setStaleLockTime(0)
            if not self.lock.tryLock(0):
                raise RuntimeError("Could not initialize an independent analyzer session")
            self.current = {"version": 1, "clean_exit": False, "ui": preferences, "plugin": None}
            self._write()
        self.previous = {}  # Last on-disk session, retained for startup decisions.
        self.current = {}  # Current dirty configuration, published atomically.
        self.preset_path = self.directory / f"plugin-{uuid.uuid4().hex}.vstpreset"  # Unique new state sidecar.
        if directory is None and self._temporary_directory is None and not self.path.exists():
            # Qt resolves the former platform-specific location without native platform APIs.
            previous = Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
            try:
                self._migrate_previous(previous)
            except Exception:
                self.lock.unlock()
                raise

    def _migrate_previous(self, directory):
        """Copy the old configuration and referenced preset once, preserving clean-exit gating."""
        source = directory / "config.json"
        if directory == self.directory or not source.is_file():
            return
        # Do not copy stale state while an older application instance is still writing it.
        previous_lock = QLockFile(str(directory / "session.lock"))
        previous_lock.setStaleLockTime(0)
        if not previous_lock.tryLock(0):
            raise RuntimeError("Close the analyzer using the previous configuration directory before restarting")
        try:
            try:
                values = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            if not isinstance(values, dict) or values.get("version") != 1:
                return
            plugin = values.get("plugin")
            name = plugin.get("preset") if isinstance(plugin, dict) else None
            if name:
                if not isinstance(name, str) or Path(name).name != name:
                    raise ValueError("Invalid saved preset filename in previous configuration")
                if (directory / name).is_file():
                    # Publish the referenced sidecar before publishing a config that points to it.
                    descriptor, temporary = tempfile.mkstemp(prefix="migrate-", suffix=".tmp", dir=self.directory)
                    try:
                        with os.fdopen(descriptor, "wb") as output, (directory / name).open("rb") as input_stream:
                            shutil.copyfileobj(input_stream, output)
                            output.flush()
                            os.fsync(output.fileno())
                        os.replace(temporary, self.directory / name)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                else:
                    # An incomplete saved state must not authorize automatic plugin loading.
                    values["clean_exit"] = False
            self.current = values
            self._write()
        finally:
            previous_lock.unlock()


    def _write(self):
        """Replace configuration atomically after flushing its complete JSON contents."""
        # A crash during a write leaves either the prior complete file or the new complete file.
        descriptor, temporary = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(self.current, stream, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def begin(self):
        """Read previous UI state, then persist an unclean marker before any plugin is loaded."""
        # Invalid/truncated configuration never authorizes plugin auto-loading.
        try:
            previous = json.loads(self.path.read_text(encoding="utf-8"))
            self.previous = previous if isinstance(previous, dict) and previous.get("version") == 1 else {}
        except (OSError, ValueError):
            self.previous = {}
        if not isinstance(self.previous.get("ui"), dict):
            self.previous["ui"] = {}
        self.current = {"version": 1, "clean_exit": False,
                        "ui": self.previous["ui"], "plugin": self.previous.get("plugin")}
        self._write()
        return self.previous

    def update_ui(self, values):
        """Persist UI preferences without ever making a running session auto-restorable."""
        # Last-directory updates survive a later plugin crash while the dirty marker remains false.
        self.current.setdefault("ui", {}).update(values)
        self.current["clean_exit"] = False
        self._write()

    def restorable_plugin(self):
        """Return saved plugin metadata only if the immediately preceding session closed cleanly."""
        # UI preferences can be restored after a crash; native plugin loading must be explicitly gated.
        plugin = self.previous.get("plugin")
        return plugin if self.previous.get("clean_exit") is True and isinstance(plugin, dict) else None

    def finish(self, plugin, clean):
        """Commit the saved state only after the worker confirms graceful native shutdown."""
        # A unique preset sidecar avoids overwriting the previous known-good state during capture.
        if plugin and plugin.get("preset"):
            with self.preset_path.open("rb") as stream:
                os.fsync(stream.fileno())
        self.current["plugin"] = plugin
        self.current["clean_exit"] = bool(clean)
        self._write()
        if clean:
            # Remove only this application's superseded preset sidecars after the new config is durable.
            keep = plugin.get("preset") if plugin else None
            for path in self.directory.glob("plugin-*.vstpreset"):
                if path.name != keep:
                    path.unlink(missing_ok=True)
        self.lock.unlock()
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()

    def preset_for(self, plugin):
        """Resolve an owned preset filename without allowing configuration paths to escape its directory."""
        # Native state is optional for plugins supporting only parameter-list restoration.
        name = plugin.get("preset")
        if not name:
            return None
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("Invalid saved preset filename")
        return str(self.directory / name)
