# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Distribution GUI entry point with a state-free dependency/IPC smoke check."""
from pathlib import Path
import json
import platform
import sys

# Source/native-host tar distributions use the system interpreter and adjacent sources.
if not getattr(sys, "frozen", False):
    root = Path(__file__).resolve().parent
    if not (root / "python/vst3_analyzer").is_dir() and (root.parent.parent / "python/vst3_analyzer").is_dir():
        root = root.parent.parent
    sys.path.insert(0, str(root / "python"))

from vst3_analyzer.ui.__main__ import main


def bundle_check(report_path):
    """Check packaged dependencies and native IPC, writing results to report_path without loading a plugin."""
    import numpy as np
    import PySide6
    import pyqtgraph
    from vst3_analyzer.ipc.client import HostClient
    from vst3_analyzer.ui.runtime import default_host_executable

    # Avoid SessionStore: the check must not alter the user's clean-exit marker.
    host = default_host_executable()
    asset_root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "python"
    asset = asset_root / "vst3_analyzer" / "ui" / "assets" / "VST3_Icon.ico"
    if not asset.is_file():
        raise RuntimeError("Bundled application icon is missing")
    transformed = np.fft.rfft(np.array([1., 0., 0., 0.], dtype=np.float64))
    if transformed.dtype != np.complex128 or not np.array_equal(transformed, np.ones(3)):
        raise RuntimeError("Bundled float64 FFT check failed")
    # Initialize the packaged Qt platform and render an actual plot without creating a user session or visible window.
    from PySide6.QtWidgets import QApplication
    from vst3_analyzer.ui.branding import configure_application_identity, application_icon
    from vst3_analyzer.ui.spectrum import SpectrumView
    from vst3_analyzer.dsp.response import analyze_frequency_response
    from vst3_analyzer.measurements.live import LiveFrame

    configure_application_identity()
    application = QApplication.instance() or QApplication([])
    if application_icon().isNull():
        raise RuntimeError("Bundled application icon could not be decoded")
    widget = SpectrumView()
    widget.resize(900, 600)
    widget.set_measurement("frequency")
    impulse = np.zeros(8192, dtype=np.float64)
    impulse[0] = 1
    response = analyze_frequency_response(impulse, 44100)
    widget.show_frame(LiveFrame((response,), True, 0, "float64", 0, 8192, measurement="frequency"))
    application.processEvents()
    preview = Path(report_path).with_suffix(".png")
    rendered = widget.grab()
    if rendered.isNull() or not rendered.save(str(preview)):
        raise RuntimeError("Bundled plot rendering failed")
    widget.close()
    with HostClient(host, initialization_timeout=30) as client:
        capabilities = client.capabilities
    report = {"ok": True, "architecture": platform.machine(), "python": platform.python_version(),
              "numpy": np.__version__, "pyside6": PySide6.__version__, "pyqtgraph": pyqtgraph.__version__,
              "host": str(host), "capabilities": capabilities, "qt_platform": application.platformName(),
              "plot_preview": str(preview)}
    Path(report_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    # A report file also works for windowed bundles whose stdout/stderr are unavailable.
    if len(sys.argv) == 3 and sys.argv[1] == "--bundle-check-report":
        try:
            bundle_check(sys.argv[2])
        except Exception as error:
            Path(sys.argv[2]).write_text(json.dumps({"ok": False, "error": str(error)}) + "\n", encoding="utf-8")
            raise
    else:
        raise SystemExit(main())
