#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Build a native Linux runtime and all release archives without root privileges.
set -eu
umask 022
[ "$(uname -s)" = Linux ] || { echo 'Run this script on Linux.' >&2; exit 1; }
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
for tool in dpkg-deb rpmbuild; do
    command -v "$tool" >/dev/null || { echo "Install $tool before packaging." >&2; exit 1; }
done
"${PYTHON:-python3}" -m venv .venv-linux-bundle
.venv-linux-bundle/bin/python -m pip install -r packaging/linux/requirements.txt
.venv-linux-bundle/bin/python -m pip install --no-deps -e .
.venv-linux-bundle/bin/cmake -S . -B build-linux-release -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF -DANALYZER_LINUX_EDITOR=ON
.venv-linux-bundle/bin/cmake --build build-linux-release --target vst3-host-service \
    --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-1}"
exec .venv-linux-bundle/bin/python scripts/linux_packages.py "$@"
