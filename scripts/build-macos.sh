#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Prepare a Universal 2 Python environment and build a self-contained, ad-hoc-signed application.
set -eu
if [ "$(uname -s)" != Darwin ]; then
    printf '%s\n' 'Build the macOS bundle on macOS.' >&2
    exit 1
fi
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
cd "$project_dir"
# Prefer an explicit or installed compatible Python; otherwise extract the official
# installer locally. This never installs or removes a system-wide Python version.
universal_python=${1:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13}
if [ ! -x "$universal_python" ]; then
    if [ "$#" -gt 0 ]; then
        printf '%s\n' "Python executable does not exist: $universal_python" >&2
        exit 1
    fi
    runtime_dir="$project_dir/.tools/macos-packaging/python313"
    package="$runtime_dir/python-3.13.16-macos11.pkg"
    mkdir -p "$runtime_dir"
    if [ ! -f "$package" ]; then
        curl --fail --location --retry 3 \
            https://www.python.org/ftp/python/3.13.16/python-3.13.16-macos11.pkg \
            --output "$package.download"
        mv "$package.download" "$package"
    fi
    # Pin the official payload and verify its Apple installer signature before extraction.
    printf '%s  %s\n' 30666509020b4da0dd8bc2e773255f34d76b7bb80b66960a928d5f6daa0192d7 "$package" | shasum -a 256 -c -
    pkgutil --check-signature "$package"
    if [ ! -d "$runtime_dir/expanded" ]; then
        pkgutil --expand-full "$package" "$runtime_dir/expanded"
    fi
    mkdir -p "$runtime_dir/runtime"
    ln -sfn "$runtime_dir/expanded/Python_Framework.pkg/Payload" "$runtime_dir/runtime/Python.framework"
    # Resolve the extracted framework and its OpenSSL libraries only during packaging.
    export DYLD_FRAMEWORK_PATH="$runtime_dir/runtime"
    export DYLD_LIBRARY_PATH="$runtime_dir/runtime/Python.framework/Versions/3.13:$runtime_dir/runtime/Python.framework/Versions/3.13/lib"
    universal_python="$runtime_dir/runtime/Python.framework/Versions/3.13/bin/python3.13"
    # Make the extracted executable relocatable for PyInstaller's arch-wrapped child
    # processes, which do not preserve DYLD_FRAMEWORK_PATH. Re-sign only this local copy.
    if otool -L "$universal_python" | grep -q '/Library/Frameworks/Python.framework/Versions/3.13/Python'; then
        install_name_tool -change /Library/Frameworks/Python.framework/Versions/3.13/Python \
            @executable_path/../Python "$universal_python"
        codesign --force --sign - "$universal_python"
    fi
    framework_launcher="$runtime_dir/runtime/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python"
    if otool -L "$framework_launcher" | grep -q '/Library/Frameworks/Python.framework/Versions/3.13/Python'; then
        install_name_tool -change /Library/Frameworks/Python.framework/Versions/3.13/Python \
            @loader_path/../../../../Python "$framework_launcher"
        codesign --force --sign - "$framework_launcher"
    fi
fi

# Check the interpreter before creating an environment; the development Homebrew Python stays untouched.
"$universal_python" scripts/macos_bundle.py check-python
"$universal_python" -m venv .venv-macos-universal-3.13
.venv-macos-universal-3.13/bin/python -m pip install -r packaging/macos/requirements.txt -e '.[gui,bundle]'
.venv-macos-universal-3.13/bin/python scripts/macos_bundle.py prepare-numpy
.venv-macos-universal-3.13/bin/python scripts/macos_bundle.py build
