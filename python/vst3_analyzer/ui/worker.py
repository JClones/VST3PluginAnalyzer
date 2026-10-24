# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""One worker owns native IPC and paced live processing; widgets remain on the GUI thread."""
from queue import Empty, Queue
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import threading
import time
from PySide6.QtCore import QThread, Signal

from ..dut.vst3 import VST3DUT
from ..ipc.client import HostClient, HostError, HostDisconnected
from ..measurements.live import LiveAnalyzer, RealtimePacer
from ..settings import MeasurementSettings
from .presets import discover_presets, validate_preset


class AnalyzerWorker(QThread):
    """Serialize control changes between audio blocks without blocking Qt's event loop."""

    shutdown_saved = Signal(object)  # Final plugin snapshot and confirmed native shutdown status.
    frame_ready = Signal()  # Latest spectrum mailbox was updated; no queued audio arrays.
    settings_applied = Signal(object)  # Confirmed settings and latency after native reconfiguration.
    loaded = Signal(object)  # Metadata/parameters/settings after successful loading or configuration.
    parameter_text_result = Signal(object)  # Parsed parameter text confirmation or recoverable validation error.
    parameters_changed = Signal(object)  # Current values and plugin-formatted strings.
    classes_ready = Signal(object)  # Discovered effect classes and the requested module path.
    programs_changed = Signal(object)  # Factory program list and current program selection.
    editor_changed = Signal(object)  # Actual native editor visibility, including native close actions.
    preset_exported = Signal(str)  # Successfully exported preset path for the Program menu.
    status = Signal(str)  # Human-readable connection/acquisition/error state.
    error = Signal(str)  # Recoverable user-visible failure.
    cleared = Signal()  # Old plugin state is no longer usable.
    performance_update = Signal(object)  # Native timing entries, completion or failure for the dedicated log window.

    def __init__(self, executable, settings=MeasurementSettings(), transport="socketpair", parent=None, endpoint=None, host_pid=None):
        """Store immutable startup settings and initialize thread-safe command/frame handoffs."""
        super().__init__(parent)
        # Only run() creates and uses the service client.
        self.endpoint = endpoint
        self.host_pid = host_pid
        self.client = None
        self.forced = False
        self.executable = executable  # Native service path.
        self.ms_mode = False  # Select paired M/S excitation or continuous output decoding.
        self.measurement = "frequency"  # Initial desktop mode; saved sessions can override it.
        self.settings = settings  # Effective settings, replaced only inside the worker.
        self.transport = transport  # Inherited pair or named Unix socket mode.
        self.commands = Queue()  # GUI-to-worker commands, processed between audio requests.
        self.wake = threading.Event()  # Interrupt pacing waits for queued edits or shutdown.
        self.stop_requested = threading.Event()  # Shutdown request observed even with a busy command queue.
        self.frame_lock = threading.Lock()  # Protect the single-frame mailbox.
        self.preset_path = None  # Optional session-state output file, supplied before the worker starts.
        self.latest_frame = None  # Newest immutable analysis frame; old frames are replaced.

    def submit(self, operation, **values):
        """Queue a control operation without accessing native objects from the GUI thread."""
        # Waking the worker services controls promptly but does not accelerate audio delivery.
        self.commands.put((operation, values))
        self.wake.set()

    def stop(self):
        """Request cooperative shutdown and interrupt an idle/pacing wait."""
        # The GUI remains responsive and can force exit while a native request is blocked.
        self.stop_requested.set()
        self.wake.set()

    def abort(self):
        """Force a dirty exit without waiting for the serialized worker request."""
        self.forced = True
        self.stop()
        if self.client is not None:
            self.client.abort()

    def take_frame(self):
        """Transfer the latest spectrum to the GUI, dropping superseded intermediate frames."""
        # A slow renderer never queues unbounded NumPy buffers.
        with self.frame_lock:
            result, self.latest_frame = self.latest_frame, None
            return result

    def _prepare_editor(self, client, state=None):
        """Open a newly loaded editor by default or restore an explicitly saved appearance."""
        # Plugins without a native view remain usable through the parameter list.
        available = client.request("has_editor")["available"]
        if available:
            desired = state if isinstance(state, dict) else {"visible": True, "geometry": None}
            try:
                actual = client.request("restore_editor", **desired)
            except HostError as error:
                # A view attachment failure must not discard a healthy audio stream.
                self.error.emit(f"Could not open plugin editor: {error}")
                actual = client.request("get_editor")
        else:
            actual = {"visible": False, "geometry": None}
        return available, actual

    def run(self):
        """Own the host lifetime, drain controls and process blocks at the selected sample rate."""
        client = HostClient(self.executable, timeout=2.0, initialization_timeout=30.0, transport=self.transport, endpoint=self.endpoint, host_pid=self.host_pid,
                            wait_forever=True)
        self.client = client
        dut = VST3DUT(client)
        stream = pacer = None
        active_plugin = None
        unsafe = False  # Native failures disallow automatic restore on the next launch.
        last_parameters = 0.0
        pending_exports = []  # File saves serviced after the next normally paced processing block.
        rate_start = 0.0  # Wall-clock start of the current paced stream.
        performance_active = False  # Live acquisition pauses until the performance window closes.
        try:
            # The interface and native host share one lifetime; startup failures end both.
            try:
                client.start()
                self.status.emit("Ready — load a VST3 effect")
            except Exception as error:
                unsafe = True
                self.stop_requested.set()
                self.status.emit(str(error))
            while not self.stop_requested.is_set() or not self.commands.empty() or pending_exports:
                self.wake.clear()
                for _ in range(0 if pending_exports else 64):
                    try:
                        operation, values = self.commands.get_nowait()
                    except Empty:
                        break
                    try:
                        if not client.capabilities:
                            raise HostDisconnected("The application host has exited")
                        if operation == "classes":
                            classes = client.request("list_classes", path=values["path"])["classes"]
                            self.classes_ready.emit({"path": values["path"], "classes": classes})
                        elif operation == "load":
                            # A fresh instance starts streaming automatically after selection.
                            stream = pacer = None
                            active_plugin = None
                            self.cleared.emit()
                            info = dut.load(values["path"], values.get("class_id", ""))
                            if values.get("preset"):
                                client.request("load_preset", path=values["preset"])
                            for parameter in values.get("parameters", []):
                                if not parameter.get("flags", 0) & 2:
                                    dut.set_parameter(parameter["id"], parameter["value"])
                            active_plugin = {"path": info["path"], "class_id": info["class_id"]}
                            stream = LiveAnalyzer(dut, self.settings, self.measurement, self.ms_mode)
                            has_editor, editor = self._prepare_editor(client, values.get("editor"))
                            rate_start = time.monotonic()
                            pacer = RealtimePacer(self.settings.sample_rate, rate_start)
                            self.loaded.emit({"info": info, "parameters": dut.parameters(), "setup": stream.setup,
                                              "programs": client.request("get_programs")["programs"],
                                              "presets": discover_presets(info["path"], info["class_id"]),
                                              "has_editor": has_editor, "editor": editor})
                            self.status.emit("Streaming — collecting FFT")
                        elif operation in ("settings", "measurement", "ms_mode"):
                            if operation == "ms_mode":
                                self.ms_mode = values["enabled"]
                                with self.frame_lock:
                                    self.latest_frame = None
                            elif operation == "measurement":
                                self.measurement = values["measurement"]
                                with self.frame_lock:
                                    self.latest_frame = None
                            else:
                                self.settings = values["settings"]
                                # Old-rate frames must not overwrite latency after the configuration acknowledgement.
                                with self.frame_lock:
                                    self.latest_frame = None
                            if stream is not None:
                                # Reconfigure only for deliberate settings changes, never parameter edits.
                                stream = LiveAnalyzer(dut, self.settings, self.measurement, self.ms_mode)
                                rate_start = time.monotonic()
                                pacer = RealtimePacer(self.settings.sample_rate, rate_start)
                                self.loaded.emit({"info": client.request("plugin_info"),
                                                  "parameters": dut.parameters(), "setup": stream.setup,
                                                  "programs": client.request("get_programs")["programs"],
                                                  "has_editor": client.request("has_editor")["available"],
                                                  "editor": client.request("get_editor")})
                                self.status.emit("Streaming — collecting FFT after settings change")
                            if operation == "settings":
                                self.settings_applied.emit({"settings": self.settings,
                                                            "latency": stream.latency if stream is not None else None})
                        elif operation == "performance_start":
                            if stream is None:
                                raise HostError("invalid_state", "Load a plugin before running Performance")
                            performance_active = True
                            with self.frame_lock:
                                self.latest_frame = None
                            # These unpaced requests time DSP only, preserving the instance, state and configuration.
                            for run in range(1, 4):
                                for stimulus, label in (("noise", "random"), ("impulse", "impulse")):
                                    result = client.request("performance", stimulus=stimulus)
                                    self.performance_update.emit({**result, "label": label, "run": run, "runs": 3})
                            self.performance_update.emit({"complete": True})
                        elif operation == "performance_end":
                            performance_active = False
                            if stream is not None:
                                stream.reset_after_performance()
                                rate_start = time.monotonic()
                                pacer = RealtimePacer(self.settings.sample_rate, rate_start)
                        elif operation == "load_preset":
                            # Validate before replacing the instance, then restore before processor activation.
                            if active_plugin is None:
                                raise HostError("invalid_state", "Load a plugin before loading a preset")
                            try:
                                validate_preset(values["path"], active_plugin["class_id"])
                            except (OSError, ValueError) as error:
                                self.error.emit(str(error))
                                continue
                            editor_state = client.request("get_editor")
                            plugin = dict(active_plugin)
                            stream = pacer = None
                            self.cleared.emit()
                            info = dut.load(plugin["path"], plugin["class_id"])
                            client.request("load_preset", path=values["path"])
                            stream = LiveAnalyzer(dut, self.settings, self.measurement, self.ms_mode)
                            has_editor, editor = self._prepare_editor(client, editor_state)
                            rate_start = time.monotonic()
                            pacer = RealtimePacer(self.settings.sample_rate, rate_start)
                            self.loaded.emit({"info": info, "parameters": dut.parameters(), "setup": stream.setup,
                                              "programs": client.request("get_programs")["programs"],
                                              "presets": discover_presets(info["path"], info["class_id"]),
                                              "preset_path": values["path"],
                                              "has_editor": has_editor, "editor": editor})
                        elif operation == "export_preset":
                            # Saving after a scheduled block captures preceding queued parameter edits.
                            if stream is None:
                                raise HostError("invalid_state", "Load a plugin before exporting a preset")
                            pending_exports.append(values["path"])
                            break
                        elif operation == "program":
                            client.request("select_program", parameter_id=values["parameter_id"], index=values["index"])
                            self.parameters_changed.emit(dut.parameters())
                            self.programs_changed.emit(client.request("get_programs")["programs"])
                        elif operation == "raise_editor":
                            # The host checks actual visibility and never reopens a closed editor.
                            self.editor_changed.emit(client.request("raise_editor"))
                        elif operation == "editor":
                            state = client.request("show_editor", visible=values["visible"])
                            self.editor_changed.emit(state)
                        elif operation == "parameter_text":
                            # Unsupported or invalid text is a recoverable edit rejection, preserving audio history.
                            try:
                                client.request("set_parameter_text", parameter_id=values["parameter_id"], text=values["text"])
                            except HostError as error:
                                self.parameter_text_result.emit({"parameter_id": values["parameter_id"], "error": str(error)})
                            else:
                                self.parameter_text_result.emit({"parameter_id": values["parameter_id"],
                                                                 "parameters": dut.parameters()})
                        elif operation == "parameter":
                            dut.set_parameter(values["parameter_id"], values["value"])
                            self.parameters_changed.emit(dut.parameters())
                            # Keep all captured samples: smoothing and zipper noise belong in the live FFT.
                            self.status.emit("Streaming — parameter edit applied; history retained")
                        elif operation == "unload":
                            stream = pacer = None
                            client.request("unload_plugin")
                            active_plugin = None
                            self.cleared.emit()
                            self.status.emit("Ready — load a VST3 effect")
                    except HostDisconnected as error:
                        unsafe = True
                        self.stop_requested.set()
                        self.status.emit(str(error))
                        break
                    except HostError as error:
                        # A rejected parameter edit can leave the existing stream healthy.
                        if operation not in ("parameter", "program", "export_preset", "editor"):
                            stream = pacer = None
                            self.cleared.emit()
                        if operation == "editor":
                            self.editor_changed.emit(False)
                        if operation == "performance_start":
                            self.performance_update.emit({"error": str(error)})
                        else:
                            self.error.emit(str(error))
                    except Exception as error:
                        unsafe = True
                        stream = pacer = None
                        self.cleared.emit()
                        if operation == "performance_start":
                            self.performance_update.emit({"error": str(error)})
                        else:
                            self.error.emit(str(error))
                if self.stop_requested.is_set() and not pending_exports:
                    if self.commands.empty():
                        break
                    continue
                try:
                    client.check_alive()
                except (HostDisconnected, OSError) as error:
                    unsafe = True
                    self.stop_requested.set()
                    self.status.emit(str(error))
                    break
                if stream is None or performance_active:
                    self.wake.wait(0.05)
                    continue

                # Use monotonic deadlines; commands can wake the wait without adding audio frames.
                delay = pacer.delay(time.monotonic())
                if delay:
                    self.wake.wait(delay)
                    continue
                try:
                    frame = stream.step()
                    pacer.advance(stream.chunk_size, time.monotonic())
                    for path in pending_exports:
                        try:
                            # Publish a completed preset atomically, preserving an existing file on failure.
                            destination = Path(path)
                            descriptor, temporary = tempfile.mkstemp(prefix=".preset-", suffix=".vstpreset",
                                                                      dir=destination.parent)
                            os.close(descriptor)
                            try:
                                client.request("save_preset", path=temporary)
                                os.replace(temporary, destination)
                            finally:
                                Path(temporary).unlink(missing_ok=True)
                            self.preset_exported.emit(path)
                        except (HostError, OSError) as error:
                            self.error.emit(str(error))
                    pending_exports.clear()
                    if frame is not None:
                        frame = replace(frame, rate_fps=stream.samples_processed / max(1e-9, time.monotonic() - rate_start))
                        with self.frame_lock:
                            self.latest_frame = frame
                        self.frame_ready.emit()
                    # Native editor and parameter updates remain visible even while FFT data is incomplete.
                    if time.monotonic() - last_parameters >= 0.5:
                        self.parameters_changed.emit(dut.parameters())
                        self.programs_changed.emit(client.request("get_programs")["programs"])
                        self.editor_changed.emit(client.request("get_editor"))
                        last_parameters = time.monotonic()
                except Exception as error:
                    pending_exports.clear()
                    unsafe = True
                    stream = pacer = None
                    self.cleared.emit()
                    if isinstance(error, HostDisconnected):
                        self.stop_requested.set()
                        self.status.emit(str(error))
                    else:
                        self.error.emit(str(error))
        finally:
            # Deliver queued final edits before reading processor state; this is only an exit snapshot.
            snapshot = None
            try:
                if active_plugin is not None and not unsafe and not self.forced:
                    if stream is None:
                        raise RuntimeError("Plugin is not in a saveable processing state")
                    stream.step()
                    snapshot = {**active_plugin, "parameters": dut.parameters(), "preset": None,
                                "editor": client.request("get_editor")}
                    if self.preset_path:
                        try:
                            client.request("save_preset", path=str(self.preset_path))
                            snapshot["preset"] = self.preset_path.name
                        except HostError as error:
                            # Plugins without getState still retain editable parameters as a fallback.
                            unsafe = True
                            self.status.emit(f"Could not save plugin state: {error}")
            except Exception as error:
                unsafe = True
                self.status.emit(f"Could not capture plugin state: {error}")
            if unsafe or self.forced:
                client.abort()
            clean = client.close() and not unsafe and not self.forced
            self.shutdown_saved.emit({"plugin": snapshot, "clean": clean})
