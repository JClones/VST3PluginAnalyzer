# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Portable application identity using Qt's public APIs."""
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version
import re
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication, QIcon

APPLICATION_NAME = "VST3 Plugin Analyzer"  # Display name and configuration directory name.
REPOSITORY_URL = "https://github.com/JClones/VST3PluginAnalyzer"


def application_version():
    """Read source-checkout version or installed/bundled distribution metadata."""
    source = Path(__file__).resolve()
    project = source.parents[3]
    if project / "python/vst3_analyzer/ui" == source.parent:
        # Editable-install metadata can lag behind a git pull or source version bump.
        try:
            match = re.search(r'^version\s*=\s*"([^"]+)"',
                              (project / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE)
            if match:
                return match[1]
        except OSError:
            pass
    try:
        return version("vst3-plugin-analyzer")
    except PackageNotFoundError:
        return "Unknown"


def configure_application_identity():
    """Set portable Qt naming before QApplication creates its platform integration."""
    # Native menu naming is left to Qt and the operating system's application packaging.
    QCoreApplication.setApplicationName(APPLICATION_NAME)
    QGuiApplication.setApplicationDisplayName(APPLICATION_NAME)


def application_icon():
    """Load the recolored VST3 icon bundled with the installed Python package."""
    # Qt selects the appropriate icon resolution for the window and running taskbar/Dock entry.
    return QIcon(str(Path(__file__).with_name("assets") / "VST3_Icon.ico"))
