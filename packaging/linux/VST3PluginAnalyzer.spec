# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Freeze the Linux GUI and isolated native host into a relocatable directory.
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_data_files
project = Path(SPECPATH).parents[1]
support = project / 'build-linux-bundle' / 'support'
host = Path(os.environ['VST3_ANALYZER_BUNDLE_HOST'])
a = Analysis([str(project / 'packaging/common/entrypoint.py')], pathex=[str(project / 'python')],
    binaries=[(str(host), 'native')],
    datas=collect_data_files('vst3_analyzer.ui', includes=['assets/*.ico', 'assets/*.svg']) +
          [(str(support / 'licenses'), 'licenses'), (str(support / 'build-info.json'), '.')],
    excludes=['tkinter', 'matplotlib', 'scipy', 'IPython', 'pytest'], noarchive=False)
# GTK is an optional Qt platform theme; the application uses Qt styling without a GTK runtime.
a.binaries = [entry for entry in a.binaries if not entry[0].endswith('/platformthemes/libqgtk3.so')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='vst3-plugin-analyzer',
          console=True, strip=False, upx=False)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='vst3-plugin-analyzer')
