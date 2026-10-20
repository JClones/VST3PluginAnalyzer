#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Run the interactive release helper without installing any Python packages.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
exec "${PYTHON:-python3}" "$project_dir/scripts/sign-notarize-macos.py" "$@"
