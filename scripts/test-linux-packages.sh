#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Inspect all release formats and smoke-test an isolated tar installation without sudo.
set -eu
# Smoke checks must not auto-launch a background session bus on a headless SSH connection.
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
output=${1:-dist/linux}
output=$(CDPATH= cd -- "$output" && pwd)
workspace=$(mktemp -d)
trap 'rm -rf "$workspace"' EXIT HUP INT TERM
# Verify transfer checksums, desktop syntax and root package ownership.
(cd "$output" && sha256sum -c ./*.sha256)
desktop-file-validate packaging/linux/vst3-plugin-analyzer.desktop
for deb in "$output"/*.deb; do
    dpkg-deb --info "$deb"
    dpkg-deb --fsys-tarfile "$deb" | tar -tvf - | awk '$2 != "root/root" { failed=1 } END { exit failed }'
    dpkg-deb --extract "$deb" "$workspace/deb"
    test -L "$workspace/deb/usr/local/bin/vst3-plugin-analyzer"
    test -f "$workspace/deb/usr/local/lib/vst3-plugin-analyzer/python/vst3_analyzer/ui/__main__.py"
    test ! -e "$workspace/deb/usr/local/lib/vst3-plugin-analyzer/_internal"
    test -z "$(find "$workspace/deb/usr/local/lib/vst3-plugin-analyzer" -name '*.pyc' -print -quit)"
    dpkg-deb -f "$deb" Depends | grep -F 'python3-pyside6.qtwidgets (>= 6.8)' >/dev/null
    dpkg-deb -f "$deb" Depends | grep -F 'python3-numpy (>= 1:2.0)' >/dev/null
    desktop-file-validate "$workspace/deb/usr/local/share/applications/vst3-plugin-analyzer.desktop"
    QT_QPA_PLATFORM=offscreen "$workspace/deb/usr/local/bin/vst3-plugin-analyzer" \
        --bundle-check-report "$workspace/deb-smoke.json"
done
for rpm in "$output"/*.rpm; do
    rpm --checksig --nosignature "$rpm"
    rpm -qp --requires "$rpm"
    # Private Qt/Python/NumPy libraries must never require packages with wheel-internal names.
    rpm -qp --requires "$rpm" | awk '/lib(Qt6|python|gfortran-|quadmath-)/ { failed=1 } END { exit failed }'
    rpm -qpl "$rpm" | sed -n '1,8p'
    mkdir -p "$workspace/rpm/usr/local/bin"
    rpm2cpio "$rpm" | (cd "$workspace/rpm" && cpio -idm --quiet --no-absolute-filenames)
    test -f "$workspace/rpm/usr/local/lib/vst3-plugin-analyzer/python/vst3_analyzer/ui/__main__.py"
    test ! -e "$workspace/rpm/usr/local/lib/vst3-plugin-analyzer/_internal"
    test -z "$(find "$workspace/rpm/usr/local/lib/vst3-plugin-analyzer" -name '*.pyc' -print -quit)"
    rpm -qp --requires "$rpm" | grep -F 'python3dist(pyside6) >= 6.8' >/dev/null
    desktop-file-validate "$workspace/rpm/usr/local/share/applications/vst3-plugin-analyzer.desktop"
    QT_QPA_PLATFORM=offscreen "$workspace/rpm/usr/local/bin/vst3-plugin-analyzer" \
        --bundle-check-report "$workspace/rpm-smoke.json"
done
# Use a temporary home so the actual user configuration, plugins and desktop stay untouched.
for archive in "$output"/*.tar.gz; do
    tar -xzf "$archive" -C "$workspace"
    stem=$(basename "$archive" .tar.gz)
    test -f "$workspace/$stem/README"
    test ! -e "$workspace/$stem/README.md"
    test -f "$workspace/$stem/runtime/python/vst3_analyzer/ui/__main__.py"
    test -x "$workspace/$stem/runtime/native/vst3-host-service"
    test ! -e "$workspace/$stem/runtime/_internal"
done
mkdir -p "$workspace/home with spaces"
export HOME="$workspace/home with spaces"
export XDG_DATA_HOME="$HOME/.local/share"
unset PYTHONPATH PYTHONHOME
for installer in "$workspace"/vst3-plugin-analyzer-*/install.sh; do
    "$installer"
done
desktop-file-validate "$XDG_DATA_HOME/applications/vst3-plugin-analyzer.desktop"
QT_QPA_PLATFORM=offscreen "$HOME/.local/bin/vst3-plugin-analyzer" \
    --bundle-check-report "$workspace/installed-smoke.json"
"$HOME/.local/lib/vst3-plugin-analyzer/uninstall.sh"
test ! -e "$HOME/.local/bin/vst3-plugin-analyzer"
echo 'Linux package inspection and isolated install/runtime/uninstall checks passed.'
