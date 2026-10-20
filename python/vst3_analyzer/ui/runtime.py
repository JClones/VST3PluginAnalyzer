# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Locate native application components independently of Finder's working directory."""
from pathlib import Path
import sys


def default_host_executable():
    """Return the native host inside a frozen bundle or the source checkout's default build."""
    # PyInstaller exposes the bundle's runtime data root on both macOS and Linux.
    if getattr(sys, "frozen", False):
        root = Path(sys._MEIPASS)
        return root / "native" / "vst3-host-service"
    # A lightweight Linux tar installs sources next to its compiled native host.
    packaged_host = Path(__file__).resolve().parents[3] / "native" / "vst3-host-service"
    if packaged_host.is_file():
        return packaged_host
    # Source launches work from any directory; an explicit --host still overrides this path.
    return Path(__file__).resolve().parents[3] / "build" / "bin" / "Release" / "vst3-host-service"
