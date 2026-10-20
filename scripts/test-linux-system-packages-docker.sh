#!/bin/bash
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Resolve system dependencies and install/run/remove packages on current target releases.
set -euo pipefail
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
output=${1:-dist/linux-ubuntu22.04}
output=$(CDPATH= cd -- "$output" && pwd)
target=${2:-ubuntu}
case "$target" in
    ubuntu)
        docker run --rm --mount "type=bind,src=$output,dst=/packages,readonly" \
            --env QT_QPA_PLATFORM=offscreen --env LANG=C.UTF-8 \
            --env DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent ubuntu:26.04 sh -ec '
                apt-get update
                DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends /packages/*.deb
                /usr/local/bin/vst3-plugin-analyzer --bundle-check-report /tmp/smoke.json
                cat /tmp/smoke.json
                apt-get remove -y vst3-plugin-analyzer
                test ! -e /usr/local/lib/vst3-plugin-analyzer
                echo "Ubuntu system package install/runtime/remove passed."
            '
        ;;
    fedora)
        docker run --rm --mount "type=bind,src=$output,dst=/packages,readonly" \
            --env QT_QPA_PLATFORM=offscreen --env LANG=C.UTF-8 \
            --env DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent fedora:44 sh -ec '
                dnf -y --setopt=install_weak_deps=False install /packages/*.rpm
                /usr/local/bin/vst3-plugin-analyzer --bundle-check-report /tmp/smoke.json
                cat /tmp/smoke.json
                dnf -y remove vst3-plugin-analyzer
                test ! -e /usr/local/lib/vst3-plugin-analyzer
                echo "Fedora system package install/runtime/remove passed."
            '
        ;;
    *) echo "Usage: $0 [package-directory] [ubuntu|fedora]" >&2; exit 2;;
esac
