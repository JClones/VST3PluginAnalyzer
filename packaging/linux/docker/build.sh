#!/bin/bash
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
set -euo pipefail
umask 022
# Only /output is writable on the host. All build inputs/environments are fresh.
export HOME=/tmp/analyzer-home
mkdir -p "$HOME" /tmp/analyzer-source
tar -C /source --exclude=__pycache__ --exclude='*.pyc' --exclude='*.egg-info' \
    -cf - CMakeLists.txt README.md LICENSE THIRD_PARTY_NOTICES.md pyproject.toml \
    'VST3 Plugin Analyzer.sh' cpp python scripts docs packaging | \
    tar -C /tmp/analyzer-source -xf -
cd /tmp/analyzer-source
if [ ! -f /source/vst3sdk/base/source/fobject.cpp ]; then
    echo "VST3 SDK submodules are missing. Run git submodule update --init --recursive in the source checkout." >&2
    exit 1
fi
mkdir vst3sdk
tar -C /source/vst3sdk --exclude=.git --exclude='._*' -cf - . | tar -C vst3sdk -xf -
{
    cat /etc/os-release
    gcc --version
    ldd --version
    python3 --version
    dpkg-query -W libc6 libstdc++6
} > /output/build-environment.txt
./scripts/build-linux-packages.sh --output /output
VST3_ANALYZER_PYTHON="$PWD/.venv-linux-bundle/bin/python" ./scripts/test-linux-packages.sh /output
