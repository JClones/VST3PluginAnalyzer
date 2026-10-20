#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Build the native macOS host and install GUI dependencies locally for source launches.
set -eu

# Resolve paths independently of the caller's working directory.
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
cd "$project_dir"
if [ "$(uname -s)" != Darwin ]; then
    printf '%s\n' 'This setup script requires macOS; see docs/linux.md for Linux.' >&2
    exit 1
fi
if [ "$#" -gt 1 ]; then
    printf '%s\n' 'Usage: ./scripts/setup-macos-source.sh [python-executable]' >&2
    exit 1
fi

# Fail before installation if the required build tools or SDK are missing.
for tool in cmake xcrun codesign; do
    command -v "$tool" >/dev/null 2>&1 || {
        printf 'Required tool is missing: %s\n' "$tool" >&2
        exit 1
    }
done
xcrun --find clang++ >/dev/null
if [ ! -f vst3sdk/CMakeLists.txt ]; then
    printf '%s\n' 'The project must include the complete vst3sdk directory.' >&2
    exit 1
fi
environment="$project_dir/.venv-macos-source"
source_python=${1:-python3}
# Updates without an explicit interpreter retain the working environment's Python.
if [ "$#" -eq 0 ] && [ -x "$environment/bin/python" ] &&
   "$environment/bin/python" -c 'import sys' >/dev/null 2>&1; then
    source_python="$environment/bin/python"
fi
"$source_python" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'
# Wheel filenames can understate Qt's actual OS requirement; check the selected profile explicitly.
"$source_python" - <<'PY'
import platform
import sys
minimum = (26, 0) if sys.version_info >= (3, 15) else (15, 0) if sys.version_info >= (3, 14) else (14, 4)
current = tuple(int(part) for part in platform.mac_ver()[0].split('.')[:2])
if current < minimum:
    raise SystemExit(f'This Python/Qt profile requires macOS {minimum[0]}.{minimum[1]} or newer. Use Python 3.13 for macOS 14.4.')
PY

# Preserve an unusable environment as a build backup, then recreate it with the selected Python.
if [ -d "$environment" ] && { [ ! -x "$environment/bin/python" ] ||
   ! "$environment/bin/python" -c 'import sys' >/dev/null 2>&1; }; then
    mkdir -p "$project_dir/build-macos-source"
    backup=$(mktemp -d "$project_dir/build-macos-source/python-environment-backup.XXXXXX")
    mv "$environment" "$backup/environment"
    printf 'Previous Python is unavailable; preserved the old environment in %s\n' "$backup/environment"
fi
# Reuse only matching working environments; an explicit Python change remains deliberate.
if [ -d "$environment" ]; then
    if ! "$source_python" - "$environment/bin/python" <<'PY'
import pathlib
import subprocess
import sys
expected = pathlib.Path(sys.base_prefix).resolve()
actual = subprocess.check_output([sys.argv[1], '-c', 'import sys; print(sys.base_prefix)'], text=True).strip()
raise SystemExit(0 if pathlib.Path(actual).resolve() == expected else 1)
PY
    then
        printf '%s\n' 'The existing .venv-macos-source uses another Python. Run setup without arguments to reuse it, or move that environment aside before changing Python.' >&2
        exit 1
    fi
else
    "$source_python" -m venv "$environment"
fi
"$environment/bin/python" -m pip install -r scripts/requirements-macos-source.txt -e '.[gui]'

# Match the host architecture to Python, including Python running under Rosetta.
architecture=$("$environment/bin/python" -c 'import platform; print(platform.machine())')
cmake -S . -B build-macos-source -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF -DCMAKE_OSX_ARCHITECTURES="$architecture"
cmake --build build-macos-source --config Release --target vst3-host-service --parallel 4

# Permit debugger attachment and loading independently signed third-party plugins.
codesign --force --sign - --entitlements packaging/macos/host.entitlements.plist \
    build-macos-source/bin/Release/vst3-host-service
"$environment/bin/python" -m pip check
printf '\n%s\n' 'Setup complete. Start the UI with: ./VST3\ Plugin\ Analyzer.sh'
