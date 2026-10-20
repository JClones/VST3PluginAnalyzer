// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Optional editor-free backend, retaining Linux plugin callbacks without linking X11.
 */
#include "host/native_editor.h"
#if defined(__linux__)
#include "host/linux_run_loop.h"
#endif
#include <stdexcept>
namespace analyzer {
bool native_editor_supported() {
    // Builds without a native window backend must not advertise an editor capability.
    return false;
}
/** Report unavailable embedding for every view in an editor-free build.
 * @param view Optional SDK editor view; unused because this build has no embedding backend.
 */
bool native_editor_view_supported(Steinberg::IPlugView* view) {
    return false;
}
/** Reject native editor creation on builds without a platform backend.
 * @param view Requested SDK view; unused because this backend does not provide native windows.
 * @param plugin_name Requested UTF-8 effect name; unused by this unavailable backend.
 */
std::unique_ptr<NativeEditor> create_native_editor(Steinberg::IPlugView* view, const std::string& plugin_name) {
    // Return a clear error instead of exposing a nonfunctional editor action.
    throw std::runtime_error("Native editor windows are disabled in this build");
}
void pump_editor_events() {
    // Headless Linux plugins can still register host-context file-descriptor and timer callbacks.
#if defined(__linux__)
    pump_linux_run_loop();
#endif
}
} // namespace analyzer
