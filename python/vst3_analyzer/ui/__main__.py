# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Launch the continuous FFT/THD desktop analyzer."""
import argparse
from pathlib import Path
import sys
from PySide6.QtWidgets import QApplication

from .branding import application_icon, configure_application_identity
from .main_window import MainWindow
from .session import SessionStore
from .runtime import default_host_executable
from ..settings import MeasurementSettings


def main(argv=None):
    """Start the GUI, optionally preloading an explicit plugin class."""
    # Keep desktop dependencies optional for headless measurement and protocol users.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect-socket", help=argparse.SUPPRESS)
    parser.add_argument("--host-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--host", default=str(default_host_executable()))
    parser.add_argument("--transport", choices=("socketpair", "named"), default="socketpair")
    parser.add_argument("plugin_path", nargs="?", metavar="PLUGIN", help="Optional VST3 plugin path to load at startup")
    parser.add_argument("--plugin", help="VST3 plugin path (alternative to the positional path)")
    parser.add_argument("--class-id", default="")
    parser.add_argument("--settings")
    parser.add_argument("--set", nargs=2, action="append", metavar=("PARAM_ID", "VALUE"))
    args = parser.parse_args(argv)
    # Explicit plugin arguments override saved-session restoration, with either spelling.
    if args.plugin_path and args.plugin:
        parser.error("Specify the plugin path either positionally or with --plugin, not both")
    args.plugin = args.plugin_path or args.plugin
    if args.set and not args.plugin:
        parser.error("--set requires a plugin path")
    try:
        initial_edits = [(int(identifier), float(value)) for identifier, value in args.set or []]
    except ValueError as error:
        parser.error(str(error))
    configure_application_identity()
    application = QApplication(sys.argv[:1])
    application.setWindowIcon(application_icon())
    try:
        session = SessionStore()
        previous = session.begin()
        stored_settings = previous.get("ui", {}).get("settings", {})
        settings = MeasurementSettings.load(args.settings) if args.settings else MeasurementSettings.from_dict(stored_settings)
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        parser.exit(1, f"Could not initialize session: {error}\n")
    window = MainWindow(str(Path(args.host).resolve()), settings, args.transport, session=session,
                        endpoint=args.connect_socket, host_pid=args.host_pid)
    window.show()
    if args.plugin:
        if args.set:
            def apply_initial_edits(_result):
                """Apply startup controls once after either direct or interactive class selection."""
                # Disconnect before submitting so later reconfiguration never reapplies startup values.
                window.worker.loaded.disconnect(apply_initial_edits)
                for identifier, value in initial_edits:
                    window.worker.submit("parameter", parameter_id=identifier, value=value)

            window.worker.loaded.connect(apply_initial_edits)
        window.load_plugin(args.plugin, args.class_id)
    elif session.restorable_plugin():
        window.restore_plugin(session.restorable_plugin())
    return application.exec()


if __name__ == "__main__":
    # Preserve the Qt application's exit code for desktop launchers and shell users.
    raise SystemExit(main())
