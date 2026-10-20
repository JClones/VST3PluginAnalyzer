#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Install a prepared source runtime and its desktop entry; use sudo for the default /usr prefix.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 "$project_dir/scripts/linux_source_install.py" install "$@"
