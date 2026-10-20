#!/bin/sh
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
# Fetch the official, pinned SDK without replacing an existing SDK directory.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
cd "$project_dir"
sdk_revision=3cdf9ca5d1f5b1b21e0a86832aa4abe55607bd96
sdk_tag=v3.8.1_build_84

# A Git checkout records the exact SDK revision in its submodule entry.
if [ -f .gitmodules ] && [ "$(git rev-parse --show-toplevel 2>/dev/null || true)" = "$project_dir" ]; then
    git submodule update --init --recursive -- vst3sdk
    exit 0
fi
# Source archives have no Git metadata; fetch a complete SDK if it is absent.
if [ -d vst3sdk ] && [ -z "$(ls -A vst3sdk)" ]; then
    rmdir vst3sdk
fi
if [ -e vst3sdk ] || [ -L vst3sdk ]; then
    [ -f vst3sdk/base/source/fobject.cpp ] && [ -f vst3sdk/public.sdk/CMakeLists.txt ] || {
        printf '%s\n' 'The SDK is incomplete. Initialize its submodules or provide the complete SDK.' >&2
        exit 1
    }
    printf '%s\n' 'Using the existing VST3 SDK.'
    exit 0
fi
command -v git >/dev/null 2>&1 || { printf '%s\n' 'Install Git first.' >&2; exit 1; }
temporary=$(mktemp -d "$project_dir/.vst3sdk-fetch.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM

# Check the tag's exact commit before retrieving its pinned submodules.
git clone --depth 1 --branch "$sdk_tag" https://github.com/steinbergmedia/vst3sdk.git "$temporary/sdk"
actual=$(git -C "$temporary/sdk" rev-parse HEAD)
if [ "$actual" != "$sdk_revision" ]; then
    printf '%s\n' 'SDK tag no longer matches the pinned commit; refusing to use it.' >&2
    exit 1
fi
git -C "$temporary/sdk" submodule update --init --recursive --depth 1
# The staging directory is on the same filesystem, so the finished SDK moves atomically.
mv "$temporary/sdk" vst3sdk
printf '%s\n' "VST3 SDK ready: $sdk_tag ($sdk_revision)"
