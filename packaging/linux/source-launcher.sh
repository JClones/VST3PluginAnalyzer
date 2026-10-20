#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
set -eu
runtime=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
interpreter=${VST3_ANALYZER_PYTHON:-python3}
if [ -z "${VST3_ANALYZER_PYTHON:-}" ] && [ -f "$runtime/.python-interpreter" ]; then
    IFS= read -r interpreter < "$runtime/.python-interpreter"
fi
case "$interpreter" in
    /*) ;;
    *) if [ -f "$runtime/$interpreter" ]; then interpreter="$runtime/$interpreter"; fi;;
esac
exec "$interpreter" -B -s "$runtime/entrypoint.py" "$@"
