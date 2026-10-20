# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
FROM ubuntu:22.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates git build-essential python3-dev python3-venv libx11-dev \
    rpm cpio desktop-file-utils xvfb xauth \
    libgl1 libegl1 libopengl0 libdrm2 libwayland-client0 libwayland-cursor0 libwayland-egl1 \
    libdbus-1-3 libfontconfig1 libfreetype6 libglib2.0-bin libzstd1 libxkbcommon-x11-0 \
    libx11-xcb1 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 \
    libxcb-xinerama0 libxcb-image0 libxcb-render-util0 libxcb-randr0 libxcb-sync1 \
    libxcb-xfixes0 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
COPY build.sh /usr/local/bin/build-analyzer-packages
ENTRYPOINT ["/usr/local/bin/build-analyzer-packages"]
