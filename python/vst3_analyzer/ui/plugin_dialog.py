# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Linux bundle selection with visible hidden folders and library navigation."""
from pathlib import Path

from PySide6.QtCore import QDir
from PySide6.QtWidgets import QFileDialog


class PluginDirectoryDialog(QFileDialog):
    """Browse ordinary directories, accepting only a named VST3 bundle."""

    def __init__(self, parent=None, directory=""):
        """Start in the remembered library or the user's hidden Linux VST3 folder."""
        super().__init__(parent, "Select a .vst3 plugin bundle")
        self.bundle_path = ""  # Accepted bundle root, independent of Qt's navigation selection.
        # Qt's own picker lets us consistently expose hidden folders across desktops.
        self.setOption(QFileDialog.DontUseNativeDialog, True)
        self.setFileMode(QFileDialog.Directory)
        self.setOption(QFileDialog.ShowDirsOnly, True)
        self.setFilter(QDir.AllDirs | QDir.Hidden | QDir.NoDotAndDotDot)
        self.setDirectory(str(Path(directory).expanduser()) if directory else str(Path.home() / ".vst3"))
        self.setLabelText(QFileDialog.Accept, "Select plugin")
        self.directoryEntered.connect(self._accept_bundle)

    def _accept_bundle(self, directory):
        """Accept an entered bundle containing a Linux module; return whether it matched."""
        path = Path(directory).expanduser()
        # A library named .vst3 and incomplete bundles remain browsable directories.
        if path.suffix.lower() != ".vst3" or not any(
                module.is_file() for module in (path / "Contents").glob("*-linux/*.so")):
            return False
        self.bundle_path = str(path)
        self.done(QFileDialog.Accepted)
        return True

    def accept(self):
        """Enter library folders instead of handing them to the native module loader."""
        selected = self.selectedFiles()
        if not selected:
            return
        path = Path(selected[0]).expanduser()
        if not path.is_dir():
            return
        if not self._accept_bundle(path):
            self.setDirectory(str(path))
