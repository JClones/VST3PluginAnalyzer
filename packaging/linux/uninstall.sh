#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Remove only a marked tar installation, preserving plugin and configuration files.
set -eu
[ "$#" -eq 0 ] || { echo 'Usage: ./uninstall.sh (or sudo ./uninstall.sh for all users)' >&2; exit 2; }
if [ "$(id -u)" = 0 ]; then
    prefix=/usr/local
else
    prefix="$HOME/.local"
fi
install_dir="$prefix/lib/vst3-plugin-analyzer"
[ -f "$install_dir/.analyzer-install" ] || { echo 'No marked tar installation found (use the package manager for .deb/.rpm).' >&2; exit 1; }
IFS= read -r data_dir < "$install_dir/.analyzer-install"
case "$data_dir" in /*) ;; *) echo 'Invalid installation marker.' >&2; exit 1;; esac
# Leave a launcher replaced by the user or another installation intact.
if [ "$(readlink "$prefix/bin/vst3-plugin-analyzer" || true)" = "$install_dir/vst3-plugin-analyzer" ]; then
    rm "$prefix/bin/vst3-plugin-analyzer"
fi
rm -f "$data_dir/applications/vst3-plugin-analyzer.desktop" "$data_dir/pixmaps/vst3-plugin-analyzer.svg"
rm -rf "$install_dir"
if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database "$data_dir/applications" || true; fi
echo 'Application and its managed Python environment removed. Settings, plugins and system libraries were preserved.'
