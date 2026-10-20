// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Serialized VST3 Linux timer/file-descriptor callbacks, independent of native windows.
 */
#pragma once
#include "pluginterfaces/gui/iplugview.h"

namespace analyzer {
/** Return the process-wide Linux run loop; registrations belong to the sole hosted plugin. */
Steinberg::Linux::IRunLoop& linux_run_loop();
/** Dispatch ready descriptors and due timers without sleeping or monopolizing audio IPC. */
void pump_linux_run_loop();
/** Release outstanding callbacks before unloading the plugin's native code. */
void clear_linux_run_loop();
} // namespace analyzer
