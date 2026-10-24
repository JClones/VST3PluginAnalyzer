# Linux installation

## Choose an installation method

Use the Debian package on Ubuntu 26.04 or the RPM package on Fedora 44 for the
simplest installation. APT/DNF installs compatible Python dependencies from your
enabled repositories. Other releases need matching dependency versions; older
Ubuntu releases can use the tar archive or source setup below.

```sh
# Ubuntu: run in the download directory.
sudo apt install ./vst3-plugin-analyzer_0.2.0_amd64.deb
# Fedora:
sudo dnf install ./vst3-plugin-analyzer-0.2.0-1.x86_64.rpm
```

Replace the filenames with those you downloaded. For another build of an already
installed version, use `apt install --reinstall` or `dnf reinstall`. Launch
**VST3 Plugin Analyzer** from the applications menu. Remove with
`sudo apt remove vst3-plugin-analyzer` or `sudo dnf remove vst3-plugin-analyzer`.

## Tar archive

Extract the archive and open a terminal in its directory. Install for your account:

```sh
./install.sh
```

For all users, run:

```sh
sudo ./install.sh
```

The installer detects APT or DNF and installs missing system prerequisites,
including Python/venv support and desktop libraries. A per-user installation may
ask for sudo to install those OS packages; application files remain user-owned.

Compatible system Python libraries are reused when available. Otherwise the
installer creates a private environment inside the application directory and
installs the required libraries. This handles Ubuntu 22.04/24.04 automatically;
there is no need to run pip or choose a Python interpreter manually. Missing
dependencies require an internet connection. Other distributions need their OS
libraries and Python 3.10 or newer installed beforehand.

User installations go under `~/.local`, all-user installations under `/usr/local`.
The private environment has the same ownership as the application. Launch from
the applications menu or use `~/.local/bin/vst3-plugin-analyzer` (your account)
or `/usr/local/bin/vst3-plugin-analyzer` (all users).

To uninstall your account's installation:

```sh
~/.local/lib/vst3-plugin-analyzer/uninstall.sh
```

For an all-user installation:

```sh
sudo /usr/local/lib/vst3-plugin-analyzer/uninstall.sh
```

The archive's `./uninstall.sh` also selects the installation by whether it runs
with sudo. Uninstallation removes the application and its managed environment,
while preserving settings, plugins, system libraries and external Python
environments. A separately created environment from an earlier manual setup
is preserved too; it can be removed separately once no installation uses it.

To update, close the application and run the new archive's installer using the
same privileges. If Python dependency setup fails, the previous application is
restored. Remove a source/package-manager installation before switching to a
system-wide tar installation. The archive README states its CPU/system-library
requirements and includes these install/uninstall instructions.

## Build from source

On Ubuntu, install the compiler, build tools and desktop-library prerequisites:

```sh
sudo apt update
sudo apt install git build-essential cmake python3 python3-venv libx11-dev \
  libgl1 libegl1 libopengl0 libdbus-1-3 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 \
  libxcb-xinerama0 libxcb-image0 libxcb-render-util0 fonts-liberation \
  libx11-xcb1 libxcb-randr0 libxcb-sync1 libxcb-xfixes0 \
  libfontconfig1 libglib2.0-bin libzstd1 libdrm2 \
  libwayland-client0 libwayland-cursor0 libwayland-egl1 \
  desktop-file-utils hicolor-icon-theme
```

On Fedora, the corresponding prerequisites are:

```sh
sudo dnf install git gcc-c++ cmake python3 python3-pip libX11-devel \
  mesa-libGL mesa-libEGL libglvnd-opengl dbus-libs libxkbcommon-x11 \
  xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image \
  xcb-util-renderutil fontconfig glib2 libdrm libwayland-client libwayland-cursor libwayland-egl \
  desktop-file-utils hicolor-icon-theme default-fonts-core-sans
```

Python 3.10 or newer and a C++17 compiler are needed. CMake 3.25 or newer is used;
the helper installs a compatible CMake locally if the system version is too old.
Other distributions use different system-package names.

Clone the repository with all submodules:

```sh
git clone --recurse-submodules https://github.com/JClones/VST3PluginAnalyzer.git
cd VST3PluginAnalyzer
```

For an existing clone, initialize missing submodules with
`git submodule update --init --recursive`. Then run as your normal user:

```sh
./scripts/build-linux.sh
"./VST3 Plugin Analyzer.sh"
```

Build setup downloads Python dependencies into
`.venv` inside the project; it does not change system Python. The source and its
local environment must stay in place while running this way. On a machine with
limited memory, use `CMAKE_BUILD_PARALLEL_LEVEL=1 ./scripts/build-linux.sh`.

To install the completed build for all users, including an application-menu entry:

```sh
./scripts/prepare-linux-install.sh
sudo ./scripts/install-linux-source.sh
```

Preparation downloads the dependencies needed for a separate installed
environment. Only the last command needs sudo; it copies the prepared application
without running a package manager or accessing the network. The installation
uses `/usr/lib/vst3-plugin-analyzer`, `/usr/bin/vst3-plugin-analyzer` and the usual
system desktop/icon directories. Keep the system Python version used for setup
installed. After installation, the source directory is no longer needed to run it.

To update, close the application, update the sources and repeat build, prepare
and install. To remove a source installation:

```sh
sudo python3 /usr/lib/vst3-plugin-analyzer/linux_source_install.py uninstall
```

## Plugins, settings and editor windows

Install Linux VST3 plugins matching the application's CPU architecture. Common
locations are `~/.vst3`, `/usr/lib/vst3` and `/usr/local/lib/vst3`. Select the whole
`.vst3` bundle in the picker. Plugins from macOS or Windows cannot be loaded.

Plugin editors need X11 or XWayland. If the analyzer opens but the plugin editor
does not, check that your desktop provides X11/XWayland and that the plugin's own
system libraries are installed. The analyzer does not need an audio interface,
JACK, ALSA configuration or audio-device permissions.

Settings and saved plugin state are under `~/.config/VST3 Plugin Analyzer/`.
Uninstalling the application preserves those files and your plugins. Close one
installation before launching another to avoid conflicting session updates.
