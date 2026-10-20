# VST3 Plugin Analyzer

A lightweight VST3 effect analyzer inspired by Christian Budde's VST Plugin
Analyzer. A C++ host loads the plugin in a separate process; a Python/Qt interface
provides measurements, interactive plots, and parameter controls. No audio interface is required.

You can measure magnitude, phase and group delay; inspect harmonic distortion
with THD and THD+N; view impulse responses; and compare stereo or Mid/Side
results. Open the plugin's own editor or the analyzer's parameter list, save
VST3 presets, and export plots as PNG, SVG or CSV.

## Compatibility and limitations

| Platform | Requirements |
| --- | --- |
| macOS | macOS 14.4 or newer. Apple Silicon and Intel architectures supported. |
| Linux | x86_64, glibc 2.35 or newer. Ubuntu 22.04, 24.04, 26.04 and Fedora 44 were tested. |
| Linux packages | Debian package for Ubuntu 26.04; RPM package for Fedora 44. For older releases, use the tar archive's installer or build from source. |

Plugins are installed separately. Use native **VST3 audio effects** matching your
OS and CPU architecture. The analyzer supplies audio test signals to the plugin's
main inputs; external sidechain inputs receive silence.

Harmonic measurements use a sine aligned to an FFT bin, so the actual frequency
may differ slightly from the requested one. Magnitude, phase and group delay
measurements feed a unity impulse into the plugin. In M/S mode, a pair of impulse
measurements measures the Mid and Side channels separately. Processing uses the
plugin's supported precision; analysis uses 64-bit floating point precision.

## Install

Download the distribution for your platform from the [Releases](https://github.com/JClones/VST3PluginAnalyzer/releases)
page. Source downloads alone are not ready-to-run applications.

### macOS: application bundle

1. Download and extract the macOS application archive.
2. Move **VST3 Plugin Analyzer.app** to **Applications**.
3. Open the application, then load an installed VST3 plugin.

Python and the required libraries are included in the Mac application. To uninstall, quit the application and move it from Applications to the Trash.
Your saved settings and separately installed plugins are retained.

### Ubuntu: Debian package

Open a terminal in the directory containing the downloaded file:

```sh
sudo apt install ./vst3-plugin-analyzer_0.1.0_amd64.deb
```

APT installs the required Python, PySide6, NumPy and pyqtgraph packages from your
enabled repositories. Ubuntu 26.04 provides compatible versions. If your release
cannot satisfy the dependencies, use **Linux: tar archive** instructions instead.
Launch **VST3 Plugin Analyzer** from the applications menu.

To uninstall:

```sh
sudo apt remove vst3-plugin-analyzer
```

### Fedora: RPM package

```sh
sudo dnf install ./vst3-plugin-analyzer-0.1.0-1.x86_64.rpm
```

DNF installs the required dependencies automatically. Fedora 44 has been checked.
Launch **VST3 Plugin Analyzer** from the applications menu. To uninstall:

```sh
sudo dnf remove vst3-plugin-analyzer
```

Use APT or DNF rather than `dpkg -i` or `rpm -i` when dependencies need downloading.
Replace the filenames above with those of the version you downloaded. When
replacing an installed package with another build of the same version, use
`apt install --reinstall` or `dnf reinstall` with the downloaded file.

### Linux: tar archive

The tar archive contains the application files and installation/uninstallation scripts.

```sh
tar -xzf vst3-plugin-analyzer-0.1.0-linux-x86_64.tar.gz
cd vst3-plugin-analyzer-0.1.0-linux-x86_64
```

Install for your account:

```sh
./install.sh
```

Or install for all users:

```sh
sudo ./install.sh
```

The installer prepares missing system libraries and Python dependencies
automatically on Ubuntu/Debian and Fedora. It may request sudo for system
libraries during a per-user installation. An internet connection is needed for
missing dependencies. System Python is unchanged; a private Python environment
is created inside the application directory when needed.

Launch **VST3 Plugin Analyzer** from the applications menu. The command is
`~/.local/bin/vst3-plugin-analyzer` for your account or
`/usr/local/bin/vst3-plugin-analyzer` for all users.

To uninstall your account's installation:

```sh
~/.local/lib/vst3-plugin-analyzer/uninstall.sh
```

To uninstall the all-user installation:

```sh
sudo /usr/local/lib/vst3-plugin-analyzer/uninstall.sh
```

The uninstaller removes the application and its managed Python environment,
preserving settings and plugins. Remove an existing package or source installation
before switching to a system-wide tar installation. The archive's **README**
also includes installation, updating and removal instructions.

## Build and install from source

Use this option if you prefer to build the application yourself. Clone the
repository with its VST3 SDK submodule and nested submodules:

```sh
git clone --recurse-submodules https://github.com/JClones/VST3PluginAnalyzer.git
cd VST3PluginAnalyzer
```

If you already cloned without submodules, run this inside the project directory:

```sh
git submodule update --init --recursive
```

The setup commands below download the required Python dependencies; an internet
connection is required.

### macOS

Install Xcode command-line tools (`xcode-select --install`), CMake 3.25 or newer,
and Python with pip and venv support. Use Python 3.13 for macOS 14.4 compatibility;
Python 3.14 requires macOS 15 or newer with this setup.

```sh
./scripts/setup-macos-source.sh python3
"./VST3 Plugin Analyzer.sh"
```

Replace `python3` with the path to the Python you want to use. Setup compiles the
plugin host and installs Python libraries locally. Keep the source directory in
place to run this version. Repeat setup after updating the sources.
See [macOS source installation](docs/macos-source.md) for details and removal.

To build a standalone application instead, run `./scripts/build-macos.sh` from
the cloned project directory. Move the resulting `dist/VST3 Plugin Analyzer.app` into
Applications. This application includes its Python runtime and required libraries.

### Linux

Install the system prerequisites listed in [Linux installation](docs/linux.md),
then run as your normal user:

```sh
./scripts/build-linux.sh
"./VST3 Plugin Analyzer.sh"
```

To install the completed build with an application-menu entry:

```sh
./scripts/prepare-linux-install.sh
sudo ./scripts/install-linux-source.sh
```

Build and preparation run without sudo; the final command copies the prepared
application into the system installation. Launch it from the applications menu
or run `vst3-plugin-analyzer`. Remove a source installation with:

```sh
sudo python3 /usr/lib/vst3-plugin-analyzer/linux_source_install.py uninstall
```

The [Linux guide](docs/linux.md) includes prerequisites, updates and other options.

## Using the analyzer

### Load and adjust a plugin

Choose **Plugin → Load VST3...** and select a `.vst3` plugin bundle. On Linux the
picker starts in `~/.vst3` and shows hidden directories. Select the bundle itself,
not a file inside it. **Plugin → Recent** reopens a recently used plugin;
**Unload** closes it.

Plugins run in a separate process, so analyzer shortcuts do not work while the
plugin editor window is active: those keystrokes do not reach VST3 Plugin
Analyzer. Activate an analyzer window before using its shortcuts.

The plugin's own editor opens by default. **Window → Default Editor** or **F2**
shows or hides it. **Window → Parameter List** or **F3** opens the analyzer's
alternative controls, with sliders, checkboxes and editable values. Double-click
or Alt-click a slider to reset its default value.

Use **Program → Load... / Export...** for `.vstpreset` files. Factory programs
appear in the same menu when supplied by the plugin. **Plugin → Soft Bypass**
uses the plugin's bypass control when available.

### Choose a measurement

| Menu | What it shows |
| --- | --- |
| Plugin → Measurement → Frequency Response | Magnitude, phase and group delay; the default for a new session. Choose the view under Plot. |
| Plugin → Measurement → Harmonic Response | Output spectrum from a sine signal, with distortion readouts. Adjust the sine tone frequency and level in **Window → Settings...**. |
| Plugin → Measurement → Impulse Response | The plugin’s response to a unity impulse, allowing inspection of ringing and pre-ringing. |
| Plugin → Generator | White noise, pink noise or silence, with the output spectrum. |
| Plugin → Tests → Performance | Three processing-speed runs, each reporting noise and impulse processing times. |

**Window → Settings...** changes sample rate, FFT size, processing block size,
and sine frequency/level. The initial sample rate is 44,100 Hz and FFT size is
8,192. Larger FFT sizes reveal finer frequency detail but update more slowly.
**Apply** changes settings without closing the dialog; reported plugin latency
is displayed there too.

### Inspect, compare and save plots

Drag a rectangle from top left to bottom right to zoom. Right-drag or use
two-finger scrolling to pan. Hold **Alt** while zooming to zoom only along the
horizontal axis, or **Shift** to zoom only along the vertical axis.
**Plot → Zoom Default** restores the default range;
**Zoom Full** fits the data. **Show Cursor Values** displays values under the
pointer. **M/S Mode** switches from Left/Right to Mid/Side display.
In M/S mode, Frequency Response and Impulse Response measurements feed the plugin
a Mid-only impulse followed by a Side-only impulse. Because these are processed
in sequence, the Mid and Side plots may briefly be out of sync when plugin
parameters change.

**Plot → Store** keeps a curve for comparison while you change plugin settings.
**Clear** removes stored curves. For frequency responses, **Make Reference**
captures the current response as a reference and displays differences from it; **Clear Reference** returns to
normal display. Stored curves are not saved between sessions.

**Plot → Export...** saves a PNG image, SVG image or CSV data file, including any stored curves.
See the [usage guide](docs/usage.md) for navigation gestures and more shortcuts.

### Keyboard shortcuts and separate windows

Use **Ctrl** on Linux and **Cmd** on macOS:

| Action | Shortcut |
| --- | --- |
| Load a plugin | Ctrl/Cmd+O |
| Show/hide plugin editor | F2 |
| Show/hide parameter list | F3 |
| Frequency / harmonic response | Ctrl/Cmd+F / Ctrl/Cmd+H |
| Performance measurement | Ctrl/Cmd+P |
| Zoom in / out / default | Ctrl/Cmd+plus / Ctrl/Cmd+minus / Ctrl/Cmd+0 |
| Store / clear curves | F8 / F7 |

Hold **Alt** with the Zoom In/Out shortcuts to zoom only along the horizontal
axis, or **Shift** to zoom only along the vertical axis.

The interface is written in Python with Qt and keeps its controls and styling
simple.

### Saved sessions and problems

A normal close saves preferences, plugin state and window layout. The next launch
restores the session. After a crash or force-quit, automatic plugin loading is
skipped so you can reopen the application and select a plugin yourself.
Settings are stored in `~/.config/VST3 Plugin Analyzer/`.

You can run multiple instances; each connects to its own plugin host. The first
instance saves preferences and the session. Additional instances start with the
current preferences and no plugin loaded, and their session changes are temporary.

If a plugin crashes, its host may close and the analyzer may report a lost
connection or timeout. Try unloading and reopening the plugin or restarting the
analyzer. When reporting a problem, include the
analyzer version, OS, CPU architecture, plugin name/version and steps to reproduce.

## License and credits

See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).
The VST3 SDK and third-party libraries retain their own licenses.
VST artwork and trademarks belong to Steinberg; this is an independent project.
