#!/bin/bash
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Build Ubuntu 22.04 release packages without reusing host build environments.
set -euo pipefail
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
if [ "$#" -gt 1 ]; then
    echo "Usage: $0 [output-directory]" >&2
    exit 2
fi
output=${1:-dist/linux-ubuntu22.04}
mkdir -p "$output"
output=$(CDPATH= cd -- "$output" && pwd)
image=vst3-plugin-analyzer-builder:ubuntu22.04
docker build --pull --tag "$image" packaging/linux/docker
docker run --rm --user "$(id -u):$(id -g)" \
    --env "CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL:-1}" \
    --mount "type=bind,src=$project_dir,dst=/source,readonly" \
    --mount "type=bind,src=$output,dst=/output" \
    "$image" 2>&1 | tee "$output/build.log"
echo "Ubuntu 22.04 packages and validation log: $output"
