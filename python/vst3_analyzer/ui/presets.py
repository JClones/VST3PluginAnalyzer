# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Find standard VST3 preset files belonging to the currently loaded effect class."""
from pathlib import Path
from PySide6.QtCore import QStandardPaths


def validate_preset(path, class_id):
    """Reject unreadable files, invalid headers and presets for another effect class."""
    # Class identifiers in VST3 preset headers are 32 ASCII hexadecimal characters.
    with Path(path).open("rb") as stream:
        header = stream.read(48)
    if len(header) != 48 or header[:4] != b"VST3":
        raise ValueError("Not a VST3 preset file")
    if header[8:40].decode("ascii", errors="replace").upper() != class_id.upper():
        raise ValueError("This preset belongs to a different plugin class")


def discover_presets(plugin_path, class_id):
    """List class-matching user, system and bundled presets, without trusting filenames."""
    # Search only standard audio preset locations and the selected plugin's resource directory.
    home = Path.home()
    roots = [home / ".vst3/presets", home / ".local/share/vst3/presets", Path("/usr/share/vst3/presets"),
             home / "Library/Audio/Presets", Path("/Library/Audio/Presets"), Path("/System/Library/Audio/Presets"),
             Path(plugin_path) / "Contents/Resources"]
    # Qt includes platform data directories and Linux XDG overrides without OS-specific calls.
    roots.extend(Path(directory) / "vst3/presets"
                 for directory in QStandardPaths.standardLocations(QStandardPaths.GenericDataLocation))
    presets = {}
    for root in dict.fromkeys(roots):
        if not root.is_dir():
            continue
        for path in root.rglob("*.vstpreset"):
            try:
                validate_preset(path, class_id)
                resolved = str(path.resolve())
                presets[resolved] = {"path": resolved, "name": path.stem}
            except (OSError, ValueError):
                # Other plugins and inaccessible files must not prevent loading this effect.
                continue
    return sorted(presets.values(), key=lambda item: (item["name"].casefold(), item["path"]))
