#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Prepare the tested source build for installation without downloading anything as root.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ -x "$project_dir/.venv/bin/python" ] || { echo 'Run ./scripts/build-linux.sh first.' >&2; exit 1; }
exec "$project_dir/.venv/bin/python" "$project_dir/scripts/linux_source_install.py" prepare "$@"
