#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Launch from the project directory regardless of the caller's working directory.
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
cd "$project_dir" || exit 1

# Prefer the separate macOS source installation when it has been prepared.
if [ "$(uname -s)" = Darwin ] && [ -d "$project_dir/.venv-macos-source" ]; then
    source_python="$project_dir/.venv-macos-source/bin/python"
    source_host="$project_dir/build-macos-source/bin/Release/vst3-host-service"
    if [ ! -x "$source_python" ] || [ ! -x "$source_host" ]; then
        printf '%s\n' 'Source setup is incomplete. Run ./scripts/setup-macos-source.sh first.' >&2
        exit 1
    fi
    export VST3_ANALYZER_PYTHON="$source_python"
    exec "$source_host" "$@"
fi

# Keep the original development/Linux environment and optional arguments supported.
if [ ! -x "$project_dir/.venv/bin/python" ]; then
    printf '%s\n' 'Python environment is missing. On macOS run ./scripts/setup-macos-source.sh; on Linux see docs/linux.md.' >&2
    exit 1
fi
exec "$project_dir/build/bin/Release/vst3-host-service" "$@"
