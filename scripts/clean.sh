#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Select a project cleanup action interactively, with preview as the default.
set -eu

# Resolve the project independently of the terminal's current directory.
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$#" -ne 0 ]; then
    printf '%s\n' 'Usage: ./scripts/clean.sh (interactive menu)' >&2
    exit 1
fi

# Delegate cleanup to the existing Python implementation so both entry points agree.
printf '%s\n' \
    'Project cleanup' \
    '  1) Preview only (no files deleted)' \
    '  2) Clean generated files, keeping dist/ releases and app bundles' \
    '  3) Clean all generated files, including dist/' \
    '  q) Cancel'
while :; do
    printf 'Choose an action [1]: '
    if ! IFS= read -r choice; then
        printf '\n%s\n' 'Cancelled.'
        exit 0
    fi
    case "$choice" in
        ''|1) exec "${PYTHON:-python3}" "$project_dir/scripts/clean.py" ;;
        2) exec "${PYTHON:-python3}" "$project_dir/scripts/clean.py" --apply --keep-dist ;;
        3) exec "${PYTHON:-python3}" "$project_dir/scripts/clean.py" --apply ;;
        q|Q) printf '%s\n' 'Cancelled.'; exit 0 ;;
        *) printf '%s\n' 'Enter 1, 2, 3, or q.' ;;
    esac
done
