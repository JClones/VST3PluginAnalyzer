# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Main measurement window with live plots, compact menus and an independent parameter editor."""
import math
from dataclasses import replace
import sys
import logging
from pathlib import Path
from PySide6.QtCore import QByteArray, QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMainWindow, QMessageBox, QWidget

from .branding import application_icon
from .native_menu import clear_native_hide_shortcut
from .plot_export import EXPORT_FILTERS, export_path, save_plot
from .settings_dialog import SettingsDialog
from .parameter_window import ParameterWindow
from .performance_dialog import PerformanceDialog
from .plugin_dialog import PluginDirectoryDialog
from .spectrum import SpectrumView
from .worker import AnalyzerWorker
from ..settings import MeasurementSettings
from ..measurements.modes import GENERATORS, MEASUREMENTS


class MainWindow(QMainWindow):
    """Keep widgets responsive while the worker continuously drives the selected VST3 effect."""

    def __init__(self, executable, settings=MeasurementSettings(), transport="socketpair", parent=None, session=None):
        """Build the analyzer shell and start an idle host worker."""
        super().__init__(parent)
        settings = replace(settings, precision="auto")  # Desktop processing always negotiates native precision.
        self.active_settings_dialog = None  # Open dialog receives live reported-latency updates.
        # Use the reference's compact sans-serif UI typography without changing native window decorations.
        interface_font = QFont("Arial")
        interface_font.setPixelSize(14)
        self.setFont(interface_font)
        self.setWindowTitle("VST3 Plugin Analyzer")
        self.setWindowIcon(application_icon())
        self.resize(1100, 680)
        self.session = session  # Optional persistent config owner; tests can supply an isolated directory.
        self.shutdown_result = {"plugin": None, "clean": False}  # Worker-confirmed final state.
        self.session_finished = False  # Prevent duplicate commits during asynchronous close.
        self.recent_plugins = []  # Up to ten successfully loaded module/class pairs, newest first.
        self.last_plugin_directory = ""  # File picker location, independent of currently loaded effect.
        self.editor_state = {"visible": False, "geometry": None}  # Latest native appearance; final capture is worker-owned.
        self.saved_plot_range = None  # Restored viewport, applied after automatic plugin loading.
        self.settings = settings  # Settings shared with the dialog and worker commands.
        self.plugin_path = ""  # Current successfully loaded module, used for explicit reload.
        self.plugin_name = ""  # Loaded effect's display name, cleared when the plugin is unloaded.
        self.class_id = ""  # Stable selected class identifier.
        self.last_frame = None  # Most recently rendered analysis record.
        self.factory_programs = []  # Current SDK program list and selection state.
        self.file_presets = []  # Class-matching VST3 preset files available in Program.
        self.last_export_directory = ""  # Most recent successful plot-export directory.
        self.last_preset_directory = ""  # Location shared by preset import and export dialogs.
        self.bypass_parameter_id = None  # Writable VST3 kIsBypass parameter, if the loaded effect exposes one.
        self.worker = AnalyzerWorker(executable, settings, transport, self)  # Sole native IPC owner.
        self.spectrum = SpectrumView()  # Large central FFT canvas.
        self.spectrum.set_measurement("frequency")  # New sessions start with Frequency Response.
        self.spectrum.set_sample_rate(settings.sample_rate, settings.fft_size)
        self.spectrum.reset_view()
        self.setCentralWidget(self.spectrum)
        self.parameter_window = ParameterWindow(self)  # Independent scrollable parameter editor.
        self.parameters = self.parameter_window.table  # Parameter rows shared with UI/session helpers.
        self.parameter_rows = self.parameter_window.rows  # Stable internal IDs for control lookup.
        self.parameter_window.normalized_requested.connect(self._set_parameter)
        self.parameter_window.text_requested.connect(self._set_parameter_text)
        self._create_menus()
        self._update_plot_title()
        QApplication.instance().installEventFilter(self)
        if self.session is not None:
            self.worker.preset_path = self.session.preset_path
            self._restore_ui(self.session.previous.get("ui", {}))

        # Signals deliver results to the GUI thread; no widget is touched by run().
        self.worker.frame_ready.connect(self._show_latest_frame)
        self.worker.loaded.connect(self._loaded)
        self.worker.settings_applied.connect(self._settings_applied)
        self.worker.parameters_changed.connect(self._update_parameters)
        self.worker.parameter_text_result.connect(self._parameter_text_result)
        self.worker.classes_ready.connect(self._select_class)
        self.worker.programs_changed.connect(self._update_programs)
        self.worker.editor_changed.connect(self._editor_changed)
        self.worker.preset_exported.connect(self._preset_exported)
        self.worker.error.connect(self._error)
        self.worker.cleared.connect(self._clear)
        self.worker.shutdown_saved.connect(self._shutdown_saved)
        self.worker.finished.connect(self._worker_finished)
        QApplication.instance().applicationStateChanged.connect(self._application_state_changed)
        self.worker.start()
        QTimer.singleShot(0, self._reserve_harmonic_menu_shortcut)

    def _reserve_harmonic_menu_shortcut(self):
        """Free the native Hide shortcut and synchronize its visible measurement replacement."""
        if clear_native_hide_shortcut():
            # Force Qt to resynchronize the key equivalent after the native duplicate has been removed.
            action = self.measurement_actions["harmonic"]
            shortcut = action.shortcut()
            action.setShortcut(QKeySequence())
            action.setShortcut(shortcut)

    def eventFilter(self, watched, event):
        """Reserve measurement and zoom keys before native menus and focused widgets handle them.

        Args:
            watched: Qt object receiving the event, restricted here to this window's widgets.
            event: Shortcut-override or key event to dispatch to the measurement or zoom command.
        """
        # Accepting ShortcutOverride prevents the macOS application menu from handling Cmd+H as Hide.
        if (event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress) and
                isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched)) and
                hasattr(self, "measurement_actions")):
            # Normalize the physical +/- keys before Qt's native shortcut matcher strips Shift
            # or treats Option's alternate character as a different shortcut. Equal also means zoom in.
            key = event.key()
            zoom_in = key in (Qt.Key_Plus, Qt.Key_Equal, ord("±"), ord("≠"))
            zoom_out = key in (Qt.Key_Minus, Qt.Key_Underscore, ord("–"), ord("—"))
            if event.modifiers() & Qt.ControlModifier and (zoom_in or zoom_out):
                event.accept()
                if event.type() == QEvent.KeyPress:
                    # Native menu dispatch can strip Shift from the key event; read held axis keys directly.
                    self.spectrum.zoom(0.8 if zoom_in else 1.25, QGuiApplication.queryKeyboardModifiers())
                return True
            sequence = QKeySequence(event.keyCombination())
            for action in (*self.measurement_actions.values(), self.zoom_default_action):
                if action.shortcut().matches(sequence) == QKeySequence.ExactMatch:
                    event.accept()
                    # Dispatch once on key press; the override only reserves the event.
                    if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
                        action.trigger()
                    return True
        return super().eventFilter(watched, event)

    def _application_state_changed(self, state):
        """Raise the visible editor when Qt reports application activation.

        Args:
            state: New Qt application state, independent of individual window focus.
        """
        # Native ordering stays in the host; changing focus between Qt windows does not trigger this.
        if state == Qt.ApplicationActive:
            self._reserve_harmonic_menu_shortcut()
            if self.editor_state["visible"]:
                self.worker.submit("raise_editor")

    def _create_menus(self):
        """Create compact file, plot and settings menus with explicit plugin lifecycle actions."""
        # Actions enqueue work; even module loading remains off the GUI thread.
        plugins = self.menuBar().addMenu("Plugin")
        self.plugin_menu = plugins  # Retain the Python wrapper across supported Qt binding versions.
        load = QAction("Load VST3...", self)
        load.setShortcut("Ctrl+O")
        load.triggered.connect(self._choose_plugin)
        plugins.addAction(load)
        self.recents_menu = plugins.addMenu("Recent")  # Ten persistent plugin shortcuts.
        self._refresh_recents()
        self.unload_action = plugins.addAction("Unload", self._unload)  # Release the current plugin.
        self.unload_action.setEnabled(False)
        self.bypass_action = plugins.addAction("Soft Bypass")  # Plugin-managed soft bypass; audio keeps running.
        self.bypass_action.setCheckable(True)
        self.bypass_action.setChecked(False)
        self.bypass_action.setEnabled(False)
        self.bypass_action.triggered.connect(self._set_bypass)
        plugins.addSeparator()
        tests_menu = plugins.addMenu("Tests")
        self.tests_menu = tests_menu  # Keep submenu wrappers alive with their parent window.
        self.performance_action = tests_menu.addAction("Performance", self._run_performance)
        self.performance_action.setShortcut("Ctrl+P")
        self.performance_action.setMenuRole(QAction.NoRole)
        self.performance_action.setEnabled(False)
        self.addAction(self.performance_action)
        measurement = plugins.addMenu("Measurement")
        self.measurement_menu = measurement  # Keep submenu wrappers alive with their parent window.
        self.measurement_group = QActionGroup(self)  # Mutually exclusive measurement choices.
        self.measurement_actions = {}  # Stable mode identifiers for menu and session state.
        for mode, title in MEASUREMENTS.items():
            action = measurement.addAction(title)
            # Qt maps Ctrl to Command on macOS, keeping these shortcuts portable.
            if mode != "impulse":
                action.setShortcut("Ctrl+F" if mode == "frequency" else "Ctrl+H")
            action.setShortcutVisibleInContextMenu(True)
            action.setMenuRole(QAction.NoRole)
            self.addAction(action)
            action.setCheckable(True)
            action.setChecked(mode == "frequency")
            self.measurement_group.addAction(action)
            self.measurement_actions[mode] = action
            action.triggered.connect(lambda checked=False, value=mode: self._select_measurement(value))
        generator = plugins.addMenu("Generator")
        self.generator_menu = generator  # Keep submenu wrappers alive with their parent window.
        self.generator_actions = {}  # Continuous generator choices share exclusivity with the Measurement submenu.
        for mode, title in GENERATORS.items():
            action = generator.addAction(title)
            action.setCheckable(True)
            action.setMenuRole(QAction.NoRole)
            self.measurement_group.addAction(action)
            self.generator_actions[mode] = action
            action.triggered.connect(lambda checked=False, value=mode: self._select_measurement(value))
        self.mode_actions = self.measurement_actions | self.generator_actions  # All persisted acquisition modes.
        self.program_menu = self.menuBar().addMenu("Program")  # Native state files and factory programs.
        self.load_preset_action = self.program_menu.addAction("Load...", self._load_preset)
        self.export_preset_action = self.program_menu.addAction("Export...", self._export_preset)
        self.load_preset_action.setEnabled(False)
        self.export_preset_action.setEnabled(False)
        self.program_menu.addSeparator()
        self.program_actions = []  # Dynamically owned program/preset actions, rebuilt on list changes.
        self.view_menu = self.menuBar().addMenu("Plot")  # Frequency-response representations.
        self.view_group = QActionGroup(self)  # One representation is selected at a time.
        self.view_actions = {}  # Stable identifiers shared with the plot and session configuration.
        for mode, title, key in (("magnitude", "Magnitude", "M"), ("wrapped", "Phase (wrapped)", "P"),
                                 ("unwrapped", "Phase (unwrapped)", "U"), ("delay", "Group Delay", "G")):
            action = self.view_menu.addAction(title)
            action.setMenuRole(QAction.NoRole)
            action.setCheckable(True)
            action.setChecked(mode == "magnitude")
            action.setShortcut(key)
            self.addAction(action)
            self.view_group.addAction(action)
            self.view_actions[mode] = action
            action.triggered.connect(lambda checked=False, value=mode: self._select_response_view(value))
        for action in self.view_actions.values():
            action.setEnabled(self.spectrum.measurement == "frequency")
        self.view_menu.addSeparator()
        self.store_action = self.view_menu.addAction("Store", self.spectrum.store_plot)
        self.clear_plots_action = self.view_menu.addAction("Clear", self.spectrum.clear_stored)
        self.reference_action = self.view_menu.addAction("Make Reference", self.spectrum.make_reference)
        self.clear_reference_action = self.view_menu.addAction("Clear Reference", self.spectrum.clear_reference)
        self.view_menu.addSeparator()
        self.export_plot_action = self.view_menu.addAction("Export...", self._export_plot)
        self.export_plot_action.setMenuRole(QAction.NoRole)
        for action, key in ((self.store_action, "F8"), (self.clear_plots_action, "F7"),
                            (self.reference_action, "F6"), (self.clear_reference_action, "F5")):
            action.setShortcut(key)
            action.setMenuRole(QAction.NoRole)
            self.addAction(action)
        self.spectrum.comparison_state_changed.connect(self._update_comparison_actions)
        self._update_comparison_actions()
        self.cursor_values_action = self.view_menu.addAction("Show Cursor Values")
        self.cursor_values_action.setCheckable(True)
        self.cursor_values_action.toggled.connect(self.spectrum.cursor.set_enabled)
        self.ms_mode_action = self.view_menu.addAction("M/S Mode")
        self.ms_mode_action.setCheckable(True)
        self.ms_mode_action.toggled.connect(self._set_ms_mode)
        self.view_menu.addSeparator()
        self.zoom_in_action = self.view_menu.addAction("Zoom In", lambda: self.spectrum.zoom(0.8, QGuiApplication.queryKeyboardModifiers()))
        self.zoom_out_action = self.view_menu.addAction("Zoom Out", lambda: self.spectrum.zoom(1.25, QGuiApplication.queryKeyboardModifiers()))
        self.zoom_default_action = self.view_menu.addAction("Zoom Default", self.spectrum.reset_view)
        self.zoom_full_action = self.view_menu.addAction("Zoom Full", lambda: self.spectrum.zoom_full(self.settings.fft_size))
        self.zoom_in_action.setShortcut("Ctrl++")
        self.zoom_out_action.setShortcut("Ctrl+-")
        self.zoom_default_action.setShortcut("Ctrl+0")
        for action in (self.zoom_in_action, self.zoom_out_action, self.zoom_default_action):
            # Register with the window as well as the native menu for portable shortcut delivery.
            action.setMenuRole(QAction.NoRole)
            self.addAction(action)
        window_menu = self.menuBar().addMenu("Window")
        self.window_menu = window_menu  # Retain the top-level menu wrapper on older bindings.
        self.editor_action = window_menu.addAction("Default Editor")  # Native editor visibility toggle.
        self.editor_action.setCheckable(True)
        self.editor_action.setEnabled(False)
        self.editor_action.toggled.connect(lambda visible: self.worker.submit("editor", visible=visible))
        self.parameter_action = window_menu.addAction("Parameter List")
        self.parameter_action.setCheckable(True)
        self.parameter_action.toggled.connect(self.parameter_window.setVisible)
        self.parameter_window.visibility_changed.connect(self.parameter_action.setChecked)
        # Keep both window toggles available while any analyzer-owned Qt window has focus.
        for action, shortcut in ((self.editor_action, "F2"), (self.parameter_action, "F3")):
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ApplicationShortcut)
            self.addAction(action)
        self.settings_action = QAction("Settings...", self)  # Keep settings in Window on every platform.
        self.settings_action.setMenuRole(QAction.NoRole)
        self.settings_action.triggered.connect(self._edit_settings)
        window_menu.addAction(self.settings_action)
        # Retain a standard application quit shortcut without adding another top-level menu.
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)
        # Older PySide releases need persistent wrappers when menus are retrieved via their actions.
        self.menu_actions = self.menuBar().actions() + plugins.actions()

    def _run_performance(self):
        """Display the native timing log and resume the selected measurement when its window closes."""
        dialog = PerformanceDialog(self.plugin_name, self)
        self.performance_action.setEnabled(False)
        self.worker.performance_update.connect(dialog.update_result)
        self.worker.submit("performance_start")
        try:
            # A modal event loop keeps drawing and worker signal delivery responsive while preventing overlapping tests.
            dialog.exec()
        finally:
            self.worker.performance_update.disconnect(dialog.update_result)
            self.worker.submit("performance_end")
            self.performance_action.setEnabled(self.unload_action.isEnabled())
            dialog.deleteLater()

    def _set_ms_mode(self, enabled):
        """Apply enabled M/S acquisition, clearing incompatible comparisons and queued display data."""
        # Channel meaning changes, so references and snapshots cannot carry across this switch.
        self.last_frame = None
        self.spectrum.set_ms_mode(enabled)
        self.worker.submit("ms_mode", enabled=enabled)

    def _select_measurement(self, measurement):
        """Switch stimulus and reset the plot to the measurement's default range."""
        # Do not reload the plugin or alter its parameters when choosing a measurement.
        if measurement == self.spectrum.measurement:
            return
        self.mode_actions[measurement].setChecked(True)
        self.saved_plot_range = None
        self.last_frame = None
        self.spectrum.set_measurement(measurement)
        for action in self.view_actions.values():
            action.setEnabled(measurement == "frequency")
        self._update_plot_title()
        self.worker.submit("measurement", measurement=measurement)

    def _update_plot_title(self):
        """Show the loaded plugin, selected measurement and frequency-response representation in the title."""
        parts = ["VST3 Plugin Analyzer"]
        if self.plugin_name:
            parts.append(self.plugin_name)
        if self.spectrum.measurement == "frequency":
            response_titles = {"magnitude": "Magnitude Response", "wrapped": "Phase Response (wrapped)",
                               "unwrapped": "Phase Response (unwrapped)", "delay": "Group Delay"}
            parts.append(response_titles[self.spectrum.response_view])
        else:
            parts.append(self.mode_actions[self.spectrum.measurement].text())
        self.setWindowTitle(" — ".join(parts))

    def _update_comparison_actions(self):
        """Keep comparison commands consistent with available frames and measurement mode."""
        has_frame = self.spectrum.latest_frame is not None
        frequency = self.spectrum.measurement == "frequency"
        self.export_plot_action.setEnabled(has_frame or any(curve.isVisible() for stored in self.spectrum.stored_plots
                                                           for curve in stored.curves))
        self.store_action.setEnabled(has_frame)
        self.clear_plots_action.setEnabled(bool(self.spectrum.stored_plots))
        self.reference_action.setEnabled(frequency and has_frame)
        self.clear_reference_action.setEnabled(frequency and self.spectrum.reference is not None)

    def _export_plot(self):
        """Choose a plot format and save the current visual or original per-bin numeric data."""
        filename, selected_filter = QFileDialog.getSaveFileName(
            self, "Export plot", self.last_export_directory, EXPORT_FILTERS, "PNG image (*.png)")
        if not filename:
            return
        try:
            # Explicit extensions take precedence; extensionless names use the selected format, defaulting to PNG.
            destination = export_path(filename, selected_filter)
            view = self.spectrum.display_view()
            traces = self.spectrum.export_traces() if destination.suffix.lower() == ".csv" else ()
            save_plot(self.spectrum, destination, traces, view)
            self.last_export_directory = str(destination.parent)
        except (OSError, ValueError, RuntimeError) as error:
            self._error(f"Could not export plot: {error}")

    def _select_response_view(self, view):
        """Change the displayed representation while retaining the current audio record.

        Args:
            view: Stable identifier of the requested frequency-response representation.
        """
        self.view_actions[view].setChecked(True)
        self.spectrum.set_response_view(view)
        self._update_plot_title()

    def _update_programs(self, programs):
        """Refresh factory program names and selected checks only when their state changes."""
        # Avoid replacing menu actions during normal polling when nothing has changed.
        if programs != self.factory_programs:
            self.factory_programs = programs
            self._rebuild_program_menu()
        else:
            # Selecting an already active checkable entry must not leave its check toggled off.
            for action, program in zip(self.program_actions, self.factory_programs):
                action.setChecked(program["selected"])

    def _rebuild_program_menu(self):
        """List available factory programs followed by matching preset files."""
        # Delete removed actions as well as removing them from the menu to bound GUI memory.
        for action in self.program_actions:
            self.program_menu.removeAction(action)
            action.deleteLater()
        self.program_actions = []
        for program in self.factory_programs:
            action = self.program_menu.addAction(program["name"].replace("&", "&&"))
            action.setCheckable(True)
            action.setChecked(program["selected"])
            action.triggered.connect(lambda checked=False, item=program:
                                     self.worker.submit("program", parameter_id=item["parameter_id"], index=item["index"]))
            self.program_actions.append(action)
        for preset in self.file_presets:
            action = self.program_menu.addAction(preset["name"].replace("&", "&&"))
            action.setToolTip(preset["path"])
            action.triggered.connect(lambda checked=False, item=preset:
                                     self.worker.submit("load_preset", path=item["path"]))
            self.program_actions.append(action)

    def _editor_changed(self, state):
        """Synchronize the menu toggle after a native editor close or host command."""
        # Updating the check state must not issue a second show/hide IPC request.
        if isinstance(state, dict):
            self.editor_state = state
        else:
            self.editor_state = {**self.editor_state, "visible": bool(state)}
        visible = self.editor_state["visible"]
        self.editor_action.blockSignals(True)
        self.editor_action.setChecked(visible)
        self.editor_action.blockSignals(False)

    def _load_preset(self):
        """Choose a native VST3 preset for the currently loaded plugin class."""
        # The worker validates class identity before replacing the running instance.
        path, _ = QFileDialog.getOpenFileName(self, "Load program", self.last_preset_directory,
                                             "VST3 presets (*.vstpreset)")
        if path:
            self.last_preset_directory = str(Path(path).parent)
            self.worker.submit("load_preset", path=str(Path(path).resolve()))

    def _export_preset(self):
        """Choose an output file for the current processor and controller state."""
        # Native save dialogs confirm overwriting; the worker captures pending edits first.
        path, _ = QFileDialog.getSaveFileName(self, "Export program", self.last_preset_directory,
                                             "VST3 presets (*.vstpreset)")
        if path:
            if not path.lower().endswith(".vstpreset"):
                path += ".vstpreset"
            self.last_preset_directory = str(Path(path).parent)
            self.worker.submit("export_preset", path=str(Path(path).resolve()))

    def _preset_exported(self, path):
        """Make a newly exported preset selectable without requiring a plugin reload."""
        # Explicit exports outside the standard preset folders also remain visible this session.
        if not any(item["path"] == path for item in self.file_presets):
            self.file_presets.append({"path": path, "name": Path(path).stem})
            self.file_presets.sort(key=lambda item: (item["name"].casefold(), item["path"]))
            self._rebuild_program_menu()

    def _refresh_recents(self):
        """Rebuild shortcuts from the most recently loaded plugin classes."""
        # Capture each entry in its callback so menu actions retain the correct target.
        self.recents_menu.clear()
        self.recents_menu.setEnabled(bool(self.recent_plugins))
        for entry in self.recent_plugins:
            name = entry.get("name") or Path(entry["path"]).name
            action = self.recents_menu.addAction(name.replace("&", "&&"))
            action.setToolTip(entry["path"])
            action.setStatusTip(entry["path"])
            action.triggered.connect(lambda checked=False, item=entry:
                                     self.load_plugin(item["path"], item["class_id"]))

    def _remember_plugin(self, info):
        """Promote a successful module/class load and immediately persist its shortcut."""
        # Failed selections never enter history; different effect classes remain distinct entries.
        entry = {"path": str(Path(info["path"]).resolve()), "class_id": info["class_id"],
                 "name": info.get("name") or Path(info["path"]).name}
        self.recent_plugins = [entry] + [item for item in self.recent_plugins
                                        if (item["path"], item["class_id"]) !=
                                        (entry["path"], entry["class_id"])]
        self.recent_plugins = self.recent_plugins[:10]
        self._refresh_recents()
        if self.session is not None:
            self.session.update_ui({"recent_plugins": self.recent_plugins})

    def load_plugin(self, path, class_id=""):
        """Load a known class or discover classes before presenting a selection."""
        # This public entry also supports startup arguments and UI smoke tests.
        path = str(Path(path).expanduser().resolve())
        self.last_plugin_directory = str(Path(path).parent)
        if self.session is not None:
            self.session.update_ui({"last_plugin_directory": self.last_plugin_directory})
        if class_id:
            self.worker.submit("load", path=path, class_id=class_id)
        else:
            self.worker.submit("classes", path=path)

    def _choose_plugin(self):
        """Select a VST3 bundle, navigating Linux libraries before loading a module."""
        # macOS exposes VST3 packages as files in native panels; directory-only panels disable them.
        directory = self.last_plugin_directory
        if sys.platform == "darwin":
            path, _ = QFileDialog.getOpenFileName(self, "Select a VST3 plugin", directory,
                                                "VST3 plugins (*.vst3)")
        else:
            # Keep hidden Linux libraries visible and distinguish them from plugin bundles.
            dialog = PluginDirectoryDialog(self, directory)
            path = dialog.bundle_path if dialog.exec() else ""
        if path:
            self.load_plugin(path)

    def _select_class(self, result):
        """Handle multi-class modules with an explicit effect choice."""
        # Continue the old plugin stream while the GUI's selection dialog is open.
        if self.worker.stop_requested.is_set():
            return
        classes = result["classes"]
        if not classes:
            self._error("Module exports no audio-effect classes")
            return
        if len(classes) == 1:
            selected = classes[0]
        else:
            labels = [f"{item['name']} — {item['id']}" for item in classes]
            chosen, accepted = QInputDialog.getItem(self, "Select effect", "Plugin class", labels, 0, False)
            if not accepted:
                return
            selected = classes[labels.index(chosen)]
        self.worker.submit("load", path=result["path"], class_id=selected["id"])

    def _loaded(self, result):
        """Refresh metadata and parameter controls after load or deliberate reconfiguration."""
        # Processor defaults remain visible; the analyzer does not silently change plugin gain.
        info, setup = result["info"], result["setup"]
        self.plugin_path, self.class_id = info["path"], info["class_id"]
        self.plugin_name = info.get("name") or Path(self.plugin_path).name
        self._remember_plugin(info)
        self.unload_action.setEnabled(True)
        self.performance_action.setEnabled(True)
        self._sync_bypass(result["parameters"])
        self.load_preset_action.setEnabled(True)
        self.export_preset_action.setEnabled(True)
        self.editor_action.setEnabled(result.get("has_editor", False))
        self._editor_changed(result.get("editor", {"visible": False, "geometry": None}))
        self.factory_programs = result.get("programs", [])
        self.file_presets = result.get("presets", self.file_presets)
        self._rebuild_program_menu()
        if result.get("preset_path"):
            self._preset_exported(result["preset_path"])
        self._update_plot_title()
        self.parameter_window.setWindowTitle(f"{self.plugin_name} — Parameter List")
        self.parameter_window.set_parameters(result["parameters"])
        if self.saved_plot_range is not None:
            self.spectrum.plot.getViewBox().setRange(xRange=self.saved_plot_range[0], yRange=self.saved_plot_range[1], padding=0)
            self.saved_plot_range = None
        else:
            self.spectrum.reset_view()

    def _sync_bypass(self, parameters):
        """Mirror the plugin's authoritative bypass value without sending another edit.

        Args:
            parameters: Current VST3 descriptors and normalized values from the controller.
        """
        # kIsBypass is defined by VST3 ParameterInfo; names and parameter IDs are plugin-specific.
        parameter = next((item for item in parameters if item["flags"] & (1 << 16)), None)
        self.bypass_parameter_id = parameter["id"] if parameter and not parameter["flags"] & 2 else None
        self.bypass_action.setEnabled(self.bypass_parameter_id is not None)
        self.bypass_action.blockSignals(True)
        self.bypass_action.setChecked(parameter is not None and parameter["value"] > 0.5)
        self.bypass_action.blockSignals(False)

    def _set_bypass(self, enabled):
        """Flag plugin-managed bypass using the ordinary queued parameter path.

        Args:
            enabled: Whether the plugin should engage its own soft-bypass implementation.
        """
        # Do not stop processing, route dry audio in the host, or reset the FFT record.
        if self.bypass_parameter_id is not None:
            self._set_parameter(self.bypass_parameter_id, 1.0 if enabled else 0.0)

    def _set_parameter(self, parameter_id, value):
        """Queue an edit between processing blocks without stopping or reconfiguring the plugin."""
        # Preserve the rolling FFT so parameter transitions and zipper noise remain visible.
        self.worker.submit("parameter", parameter_id=parameter_id, value=value)

    def _set_parameter_text(self, parameter_id, text):
        """Ask the plugin to interpret logical value text for the stable parameter_id."""
        self.worker.submit("parameter_text", parameter_id=parameter_id, text=text)

    def _parameter_text_result(self, result):
        """Confirm plugin-formatted text or restore its previous value after a rejected edit result."""
        identifier = result["parameter_id"]
        if "error" in result:
            self.parameter_window.restore_text(identifier)
            QMessageBox.warning(self.parameter_window, "Invalid parameter value", result["error"])
        else:
            self._sync_bypass(result["parameters"])
            self.parameter_window.update_parameters(result["parameters"], force_text_id=identifier)

    def _update_parameters(self, parameters):
        """Refresh authoritative parameters while preserving active slider dragging and value typing."""
        self._sync_bypass(parameters)
        self.parameter_window.update_parameters(parameters)

    def _show_latest_frame(self):
        """Render the newest spectrum with its combined distortion readout."""
        # Color choice is carried from exact waveform equality, including stereo phase.
        frame = self.worker.take_frame()
        if frame is None or frame.measurement != self.spectrum.measurement or frame.ms_mode != self.spectrum.ms_mode:
            return
        self.last_frame = frame
        if self.active_settings_dialog is not None:
            self.active_settings_dialog.set_latency(frame.latency_samples)
        self.spectrum.show_frame(frame)

    def _clear(self):
        """Clear invalid plugin traces and controls after unload, replacement or failure."""
        # Keep the selected path for a subsequent explicit selection following a native failure.
        self.spectrum.clear()
        self.plugin_name = ""
        self._update_plot_title()
        self.unload_action.setEnabled(False)
        self.performance_action.setEnabled(False)
        self._sync_bypass([])
        self.load_preset_action.setEnabled(False)
        self.export_preset_action.setEnabled(False)
        self.editor_action.setEnabled(False)
        self._editor_changed(False)
        self.factory_programs = []
        self.file_presets = []
        self._rebuild_program_menu()
        self.last_frame = None
        if self.active_settings_dialog is not None:
            self.active_settings_dialog.settings_applied(self.settings, None)
        self.worker.take_frame()
        self.parameter_window.set_parameters([])
        self.parameter_window.setWindowTitle("Parameter List")

    def _error(self, message):
        """Display actionable failures without adding a persistent debug area."""
        # The worker remains available for another load/reload request.
        logging.getLogger(__name__).error("%s", message)
        QMessageBox.warning(self, "Analyzer error", str(message))

    def _unload(self):
        """Stop processing by explicitly unloading the current instance."""
        # No pause mode is needed: a loaded live plugin is continuously driven.
        self.worker.submit("unload")

    def _reset_zoom(self):
        """Restore the default frequency and magnitude display ranges."""
        # This changes only the view, never plugin processing state or sample position.
        self.spectrum.reset_view()

    def _edit_settings(self):
        """Present validated acquisition controls while the existing tone continues."""
        # Applying new settings requests a deliberate processor reconfiguration.
        dialog = SettingsDialog(self.settings, self)
        dialog.set_latency(self.last_frame.latency_samples if self.last_frame is not None else None)
        self.active_settings_dialog = dialog
        dialog.apply_requested.connect(self._apply_settings)
        try:
            if dialog.exec() and dialog.result_settings != self.settings:
                self._apply_settings(dialog.result_settings)
        finally:
            self.active_settings_dialog = None
            dialog.deleteLater()

    def _settings_applied(self, result):
        """Refresh an open Settings dialog using confirmed settings and reported plugin latency."""
        if self.active_settings_dialog is not None:
            self.active_settings_dialog.settings_applied(result["settings"], result["latency"])

    def _apply_settings(self, settings):
        """Send one immutable settings snapshot to the worker."""
        # Preserve requested tone controls while negotiating native plugin precision automatically.
        settings = replace(settings, precision="auto")
        self.settings = settings
        self.spectrum.set_sample_rate(settings.sample_rate, settings.fft_size)
        self.worker.submit("settings", settings=settings)

    def _load_settings(self):
        """Read a settings JSON file and apply it to the live stream."""
        # Restore requested tone values along with the other validated acquisition settings.
        from dataclasses import replace
        path, _ = QFileDialog.getOpenFileName(self, "Load settings", filter="JSON (*.json)")
        if path:
            try:
                self._apply_settings(MeasurementSettings.load(path))
            except (OSError, ValueError) as error:
                self._error(str(error))

    def _save_settings(self):
        """Persist the current user settings without affecting the running tone."""
        # Serialization is small local-file work and shares the CLI settings format.
        path, _ = QFileDialog.getSaveFileName(self, "Save settings", filter="JSON (*.json)")
        if path:
            try:
                self.settings.save(path)
            except OSError as error:
                self._error(str(error))

    def closeEvent(self, event):
        """Request worker cleanup asynchronously so a hung plugin cannot freeze window closing."""
        # Keep the QThread alive until its native IPC request and bounded cleanup have finished.
        if self.worker.isRunning():
            if self.session is not None:
                self.session.update_ui(self._ui_snapshot())
            self.worker.stop()
            self.setEnabled(False)
            event.ignore()
        else:
            if self.session is not None and not self.session_finished:
                try:
                    plugin = self.shutdown_result["plugin"]
                    if plugin and "editor" in plugin:
                        # Replace the polling cache with the native host's final authoritative appearance.
                        self.session.update_ui({"editor": plugin["editor"]})
                    self.session.finish(plugin, self.shutdown_result["clean"])
                except (OSError, ValueError) as error:
                    self._error(f"Could not save session: {error}")
                self.session_finished = True
            self.parameter_window.hide()
            event.accept()

    def _worker_finished(self):
        """Finish a previously requested close after worker-owned resources are released."""
        # Avoid deleting a still-running QThread or its child-process owner.
        if self.worker.stop_requested.is_set():
            QTimer.singleShot(0, self.close)

    def _shutdown_saved(self, result):
        """Retain the worker's snapshot until the window can finish its clean close."""
        # Do not authorize auto-load while native teardown is still running.
        self.shutdown_result = result

    def _ui_snapshot(self):
        """Capture main/parameter window geometry, visibility and plot preferences as JSON-compatible values."""
        from dataclasses import asdict
        # Keep independent window geometry and visibility explicit in portable application state.
        return {"geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
                "parameter_window": {"visible": not self.parameter_window.isHidden(),
                                     "geometry": bytes(self.parameter_window.saveGeometry().toBase64()).decode("ascii"),
                                     "header": bytes(self.parameters.horizontalHeader().saveState().toBase64()).decode("ascii")},
                "last_plugin_directory": self.last_plugin_directory, "recent_plugins": self.recent_plugins,
                "last_preset_directory": self.last_preset_directory,
                "last_export_directory": self.last_export_directory,
                "editor": self.editor_state,
                "show_cursor_values": self.cursor_values_action.isChecked(),
                "ms_mode": self.ms_mode_action.isChecked(),
                "measurement": self.spectrum.measurement, "response_view": self.spectrum.response_view,
                "settings": asdict(self.settings), "plot_range": self.spectrum.plot.getViewBox().viewRange()}

    def _restore_ui(self, state):
        """Restore preferences even after an unclean exit, without touching a native plugin."""
        # Missing or malformed preferences fall back to the first-launch layout.
        if not isinstance(state, dict):
            return
        if isinstance(state.get("ms_mode"), bool):
            self.ms_mode_action.setChecked(state["ms_mode"])
        if isinstance(state.get("show_cursor_values"), bool):
            self.cursor_values_action.setChecked(state["show_cursor_values"])
        if state.get("response_view") in self.view_actions:
            self._select_response_view(state["response_view"])
        if state.get("measurement") in self.mode_actions:
            self._select_measurement(state["measurement"])
        # Restore history independently of clean-exit permission for automatic plugin loading.
        recent = state.get("recent_plugins", [])
        if isinstance(recent, list):
            seen = set()
            for entry in recent:
                if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) and entry[key]
                                                          for key in ("path", "class_id")):
                    continue
                identity = (entry["path"], entry["class_id"])
                if identity in seen:
                    continue
                seen.add(identity)
                self.recent_plugins.append({"path": entry["path"], "class_id": entry["class_id"],
                                            "name": entry.get("name") if isinstance(entry.get("name"), str) else ""})
                if len(self.recent_plugins) == 10:
                    break
        self._refresh_recents()
        export_directory = state.get("last_export_directory", "")
        self.last_export_directory = export_directory if isinstance(export_directory, str) else ""
        preset_directory = state.get("last_preset_directory", "")
        self.last_preset_directory = preset_directory if isinstance(preset_directory, str) else ""
        directory = state.get("last_plugin_directory", "")
        self.last_plugin_directory = directory if isinstance(directory, str) else ""
        parameter_state = state.get("parameter_window", {})
        if isinstance(parameter_state, dict):
            geometry = parameter_state.get("geometry")
            if isinstance(geometry, str):
                self.parameter_window.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii", errors="ignore")))
            header = parameter_state.get("header")
            if isinstance(header, str):
                self.parameters.horizontalHeader().restoreState(QByteArray.fromBase64(header.encode("ascii", errors="ignore")))
            if isinstance(parameter_state.get("visible"), bool):
                self.parameter_window.setVisible(parameter_state["visible"])
        geometry = state.get("geometry")
        if isinstance(geometry, str):
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii", errors="ignore")))
        ranges = state.get("plot_range")
        if (isinstance(ranges, list) and len(ranges) == 2 and
                all(isinstance(axis, list) and len(axis) == 2 and
                    all(isinstance(value, (int, float)) and math.isfinite(value) for value in axis) and
                    axis[0] < axis[1] for axis in ranges)):
            self.saved_plot_range = ranges
            self.spectrum.plot.getViewBox().setRange(xRange=ranges[0], yRange=ranges[1], padding=0)

    def restore_plugin(self, plugin):
        """Restore a clean-session effect and native state before realtime activation."""
        # An explicit command-line plugin bypasses this path entirely.
        try:
            preset = self.session.preset_for(plugin)
            previous_ui = self.session.previous.get("ui", {})
            editor = plugin.get("editor", previous_ui.get("editor"))
            if not isinstance(editor, dict):
                # Legacy sessions saved only visibility; missing state uses the new open-by-default policy.
                editor = {"visible": previous_ui.get("editor_visible", True), "geometry": None}
            self.worker.submit("load", path=plugin["path"], class_id=plugin["class_id"],
                               preset=preset, parameters=[] if preset else plugin.get("parameters", []), editor=editor)
        except (OSError, KeyError, ValueError) as error:
            self._error(f"Could not restore last plugin: {error}")
