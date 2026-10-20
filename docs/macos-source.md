# macOS source installation

This alternative to the `.app` bundle compiles the C++ VST3 host and runs the Python
UI from the project directory. Keep the checkout, including `vst3sdk`, in place.
No administrator access or global Python package installation is needed by the setup script.

## Prerequisites

- Xcode command-line tools: run `xcode-select --install` if they are missing.
- CMake 3.25 or newer, available as `cmake` in your shell.
- Python 3.10 or newer with `venv` and `pip`, from Python.org or Homebrew.

The pinned Qt bindings require macOS 12+ with Python 3.10–3.13, macOS 15+ with
Python 3.14, or macOS 26+ with Python 3.15. The overall source application targets
macOS 14.4 or newer. To retain macOS 14.4 support use Python 3.13.
The setup builds for the architecture of the selected Python; plugins must match
that architecture. A Universal 2 Python normally runs natively on the current Mac.

## Install or update

First clone the repository with its SDK submodules:

```sh
git clone --recurse-submodules https://github.com/JClones/VST3PluginAnalyzer.git
cd VST3PluginAnalyzer
```

If you already cloned without submodules, run
`git submodule update --init --recursive` in the project directory.
Choose your Python explicitly (Python 3.14 is shown here):

```sh
./scripts/setup-macos-source.sh /Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14
```

Without an argument, `./scripts/setup-macos-source.sh` reuses the Python in an
existing working source environment. Otherwise it uses `python3` from your shell's PATH.
The script creates `.venv-macos-source`, installs runtime dependencies and the editable
project, then builds only `vst3-host-service` in `build-macos-source`. It applies the
host's plugin-loading and debugger-attachment entitlements. Bundle-building tools
such as PyInstaller and delocate are not installed.

Repeat the same command after source changes or to finish an interrupted setup.
If the environment's Python was uninstalled or moved, the script preserves the
broken environment under `build-macos-source/python-environment-backup.*/` and
creates a new one with the selected interpreter. For example, with Homebrew Python 3.13:

```sh
./scripts/setup-macos-source.sh /opt/homebrew/bin/python3.13
```

To deliberately switch an environment that still works to a different Python,
move or remove `.venv-macos-source` first. Keep using the same CPU architecture
for a given build directory. Saved application settings are independent of this environment.

## Start

```sh
./VST3\ Plugin\ Analyzer.sh
```

Or load a plugin immediately:

```sh
./VST3\ Plugin\ Analyzer.sh "/Library/Audio/Plug-Ins/VST3/Your Plugin.vst3"
```

No environment activation is required. The starter resolves the project directory
from its own location, selects `.venv-macos-source` and passes the corresponding
host path to the UI. It also works when invoked by absolute path from another
directory. If no macOS source environment exists, it retains the original `.venv`
Linux/local-environment behavior. Additional UI arguments are forwarded unchanged.

Application settings remain in `~/.config/VST3 Plugin Analyzer/`, shared with the
bundle version. You can run both at once: the first instance saves settings,
while additional instances use temporary sessions.

## Quick verification and removal

```sh
.venv-macos-source/bin/python -m pip check
./VST3\ Plugin\ Analyzer.sh --help
```

Remove `.venv-macos-source` and `build-macos-source` to remove this local installation.
That leaves the source, `.app` bundle, other local Python environments, and saved application
settings intact.
