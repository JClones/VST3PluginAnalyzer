# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Build a Universal 2 folder-based application with a separately bundled native host.
from pathlib import Path
from importlib.metadata import version
import os
from PyInstaller.utils.hooks import collect_data_files

macos = Path(SPECPATH)
project = macos.parents[1]
support = Path(os.environ.get("VST3_ANALYZER_BUNDLE_SUPPORT", project / "build-macos-bundle"))
host = Path(os.environ.get("VST3_ANALYZER_BUNDLE_HOST", project / "build-macos-universal/bin/Release/vst3-host-service"))
minimum = os.environ.get("MACOSX_DEPLOYMENT_TARGET", "14.4")
signing_identity = os.environ.get("VST3_ANALYZER_SIGN_IDENTITY") or None  # Developer ID identity; default remains ad-hoc.

# Keep data in their package-relative locations so source and frozen resource lookups agree.
a = Analysis(
    [str(project / "packaging/common/entrypoint.py")],
    pathex=[str(project / "python")],
    binaries=[(str(host), "native")],
    datas=collect_data_files("vst3_analyzer.ui", includes=["assets/*.ico", "assets/*.svg"]) +
          [(str(support / "licenses"), "licenses"), (str(support / "build-info.json"), ".")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# Reject thin Python extensions rather than producing an application that only partly supports Intel.
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="VST3PluginAnalyzerUI", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, argv_emulation=False,
    target_arch="universal2", codesign_identity=signing_identity, entitlements_file=None,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VST3 Plugin Analyzer")
app = BUNDLE(
    collection,
    name="VST3 Plugin Analyzer.app",
    icon=str(support / "VST3_Icon.icns"),
    bundle_identifier=os.environ.get("VST3_ANALYZER_BUNDLE_ID", "org.vst3analyzer.VST3PluginAnalyzer"),
    version=version("vst3-plugin-analyzer"),
    info_plist={
        "CFBundleDisplayName": "VST3 Plugin Analyzer",
        "NSPrincipalClass": "NSApplication",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.music",
        "LSMinimumSystemVersion": minimum,
    },
)
