#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Create a local Python environment and build the native Linux plugin host.
set -eu

# Keep this helper explicit: it installs no system packages and never reuses macOS binaries intentionally.
if [ "$(uname -s)" != Linux ]; then
    printf '%s\n' 'This helper is for Linux. See README.md for the macOS build.' >&2
    exit 1
fi
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
cd "$project_dir"
python_command=${PYTHON:-python3}
build_directory=${1:-build}
editor_enabled=${ANALYZER_LINUX_EDITOR:-ON}
# Python and CMake are distribution prerequisites; all Python packages remain inside the project environment.
command -v "$python_command" >/dev/null 2>&1 || { printf '%s\n' 'Install Python 3.10+ with venv support.' >&2; exit 1; }
"$python_command" -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -e '.[gui]'

# Older Ubuntu releases may lack a recent CMake; install it only in the project environment when needed.
cmake_command=cmake
if [ -x .venv/bin/cmake ]; then
    cmake_command="$project_dir/.venv/bin/cmake"
fi
if ! .venv/bin/python - "$cmake_command" <<'PY'
import re
import subprocess
import sys
try:
    version = subprocess.check_output([sys.argv[1], '--version'], text=True)
    match = re.search(r'cmake version (\d+)\.(\d+)', version)
    raise SystemExit(0 if match and tuple(map(int, match.groups())) >= (3, 25) else 1)
except OSError:
    raise SystemExit(1)
PY
then
    .venv/bin/python -m pip install 'cmake>=3.25,<4'
    cmake_command="$project_dir/.venv/bin/cmake"
fi
"$cmake_command" -S . -B "$build_directory" -DCMAKE_BUILD_TYPE=Release \
    -DANALYZER_LINUX_EDITOR="$editor_enabled" -DBUILD_TESTING=OFF
# Bound concurrency for small VMs; callers can lower it further through CMake's standard environment variable.
build_jobs=${CMAKE_BUILD_PARALLEL_LEVEL:-$(.venv/bin/python -c 'import os; print(min(2, os.cpu_count() or 1))')}
"$cmake_command" --build "$build_directory" --parallel "$build_jobs"
# The default build matches the existing portable launcher; custom build paths can be passed with --host.
printf '\n%s\n' "Run: \"./VST3 Plugin Analyzer.sh\" --host \"$build_directory/bin/Release/vst3-host-service\""
