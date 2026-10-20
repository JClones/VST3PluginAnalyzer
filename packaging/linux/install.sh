#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Install the adjacent standalone runtime and desktop assets for a user or under /usr/local.
set -eu
# Luminance-mapped ASCII rendering of the application's SVG icon.
cat <<'ICON'

                             ...:..        .......
                           ......             .........
   ..:::::::::...     ...........           .....:...:-:::::.........
       :::::::           ............       .:.      .:::::.       ..
        .::::::.        ..  .............:..::.      .:::...        .
          .......      .        ....::::::::::--:.   .:.....
           .......    .              ..:::---------. .......
             ...... ..   ...              ..:-------  ......
              .......    .:::.               :-----.  ....::
               .....     .:::-::.          .:----:   ...::::.
                ...      .::....::::::::::::::.    ......:::::::



                     =+++++++++++++++:   -------------------
                   .*###############=   .:::::::++++++++++=.
                  :################+++++++.   .=+++++++++-
                 =###############+.   .-------++++++++++:
               .+###############+.....::::::=+++++++++=.
              :*###############+++++++.    -+++++++++=
             -###############*.  .--------=+++++++++-
             ----------------    ::::::::::::::::::.

                         VST3 Plugin Analyzer

ICON
[ "$#" -eq 0 ] || { echo 'Usage: ./install.sh (or sudo ./install.sh for all users)' >&2; exit 2; }
umask 022
system_install=0
if [ "$(id -u)" = 0 ]; then
    system_install=1
    prefix=/usr/local
    data_dir=/usr/local/share
    if [ -f /usr/lib/vst3-plugin-analyzer/.analyzer-source-install.json ]; then
        echo 'Uninstall the source installation before installing the system tar application.' >&2
        exit 1
    fi
else
    prefix="$HOME/.local"
    data_dir=${XDG_DATA_HOME:-$HOME/.local/share}
fi
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install_dir="$prefix/lib/vst3-plugin-analyzer"
bin_dir="$prefix/bin"
icon_dir="$data_dir/pixmaps"
# Refuse to replace unrelated files; the marker identifies installations made by this helper.
if [ -L "$install_dir" ] || { [ -e "$install_dir" ] && [ ! -f "$install_dir/.analyzer-install" ]; }; then
    echo "Refusing to overwrite $install_dir without installation marker." >&2; exit 1
fi
if { [ -e "$bin_dir/vst3-plugin-analyzer" ] || [ -L "$bin_dir/vst3-plugin-analyzer" ]; } &&
   [ "$(readlink "$bin_dir/vst3-plugin-analyzer" || true)" != "$install_dir/vst3-plugin-analyzer" ]; then
    echo 'Refusing to replace an unrelated launcher.' >&2; exit 1
fi
# Install missing OS prerequisites. Per-user installation requests sudo only for
# OS packages; the application and its Python environment remain user-owned.
install_os_packages() {
    if command -v apt-get >/dev/null 2>&1; then
        required='python3 python3-venv libx11-6 libstdc++6 libgl1 libegl1 libopengl0 libdbus-1-3 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 libxcb-image0 libxcb-render-util0 libx11-xcb1 libxcb-randr0 libxcb-sync1 libxcb-xfixes0 libfontconfig1 libglib2.0-bin libzstd1 libdrm2 libwayland-client0 libwayland-cursor0 libwayland-egl1 fonts-liberation'
        missing=
        for package in $required; do
            if [ "$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)" != installed ]; then
                missing="$missing $package"
            fi
        done
        [ -n "$missing" ] || return 0
        echo "Installing required system packages:$missing"
        if [ "$system_install" = 1 ]; then
            apt-get update
            apt-get install -y $missing
        else
            sudo apt-get update
            sudo apt-get install -y $missing
        fi
    elif command -v dnf >/dev/null 2>&1; then
        required='python3 python3-pip libX11 libstdc++ mesa-libGL mesa-libEGL libglvnd-opengl dbus-libs libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image xcb-util-renderutil fontconfig glib2 libdrm libwayland-client libwayland-cursor libwayland-egl'
        missing=
        for package in $required; do
            rpm -q --whatprovides "$package" >/dev/null 2>&1 || missing="$missing $package"
        done
        [ -n "$missing" ] || return 0
        echo "Installing required system packages:$missing"
        if [ "$system_install" = 1 ]; then
            dnf install -y $missing
        else
            sudo dnf install -y $missing
        fi
    else
        echo 'Automatic system dependency installation supports APT and DNF. Checking the available Python runtime.'
    fi
}
install_os_packages
interpreter=${VST3_ANALYZER_PYTHON:-python3}
command -v "$interpreter" >/dev/null 2>&1 || { echo 'Python 3.10 or newer is required.' >&2; exit 1; }
interpreter=$("$interpreter" -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10 or newer is required"; print(sys.executable)')
use_system_python=0
if "$interpreter" -B -s "$source_dir/runtime/check-dependencies.py" >/dev/null 2>&1; then
    use_system_python=1
fi
mkdir -p "$(dirname -- "$install_dir")" "$bin_dir" "$data_dir/applications" "$icon_dir"
staged_dir=$(mktemp -d "${install_dir}.new.XXXXXX")
backup_dir=
installed_new=0
committed=0
cleanup() {
    status=$?
    [ -z "$staged_dir" ] || rm -rf "$staged_dir"
    if [ "$committed" = 0 ]; then
        [ "$installed_new" = 0 ] || rm -rf "$install_dir"
        if [ -n "$backup_dir" ] && [ -d "$backup_dir" ] && [ ! -e "$install_dir" ]; then
            mv "$backup_dir" "$install_dir"
        fi
    elif [ "$committed" = 1 ] && [ -n "$backup_dir" ]; then
        rm -rf "$backup_dir"
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
cp -R "$source_dir/runtime/." "$staged_dir/"
cp "$source_dir/uninstall.sh" "$staged_dir/uninstall.sh"
printf '%s\n' "$data_dir" > "$staged_dir/.analyzer-install"
if [ -d "$install_dir" ]; then
    backup_dir=$(mktemp -d "${install_dir}.old.XXXXXX")
    rmdir "$backup_dir"
    mv "$install_dir" "$backup_dir"
fi
installed_new=1
mv "$staged_dir" "$install_dir"
staged_dir=
chmod 755 "$install_dir"
if [ "$use_system_python" = 1 ]; then
    printf '%s\n' "$interpreter" > "$install_dir/.python-interpreter"
else
    echo 'Preparing application Python environment and downloading dependencies...'
    "$interpreter" -m venv "$install_dir/venv"
    "$install_dir/venv/bin/python" -m pip install -r "$install_dir/requirements.txt"
    "$install_dir/venv/bin/python" -B -s "$install_dir/check-dependencies.py"
    # Relative to the application directory, so the launcher never depends on a
    # temporary staging path. The environment is created in its final location.
    printf '%s\n' 'venv/bin/python' > "$install_dir/.python-interpreter"
fi
ln -sfn "$install_dir/vst3-plugin-analyzer" "$bin_dir/vst3-plugin-analyzer"
# Desktop Exec needs an absolute path because graphical sessions may omit ~/.local/bin from PATH.
escaped=$(printf '%s' "$install_dir/vst3-plugin-analyzer" | sed 's/[\\"`$]/\\&/g; s/%/%%/g')
sed "s|^Exec=.*|Exec=\"$(printf '%s' "$escaped" | sed 's/[&|]/\\&/g')\"|; s|^Icon=.*|Icon=$(printf '%s' "$icon_dir/vst3-plugin-analyzer.svg" | sed 's/[&|]/\\&/g')|" \
    "$source_dir/vst3-plugin-analyzer.desktop" > "$data_dir/applications/vst3-plugin-analyzer.desktop"
cp "$source_dir/vst3-plugin-analyzer.svg" "$icon_dir/"
# Desktop cache tools are optional; desktop environments also discover entries without them.
if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database "$data_dir/applications" || true; fi
committed=1
printf 'Installed. Run "%s/vst3-plugin-analyzer" or use the applications menu.\n' "$bin_dir"
if [ "$system_install" = 1 ]; then
    printf 'Uninstall: sudo "%s/uninstall.sh"\n' "$install_dir"
else
    printf 'Uninstall: "%s/uninstall.sh"\n' "$install_dir"
fi
