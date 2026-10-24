#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
set -eu
runtime=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
exec "$runtime/native/vst3-host-service" "$@"
