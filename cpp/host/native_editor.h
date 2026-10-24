// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Platform-neutral native editor window interface and persisted visibility, position and content-size data.
 */
#pragma once
#include <cstdlib>
#include <unistd.h>
#include <memory>
#include <string>
namespace Steinberg { class IPlugView; }

namespace analyzer {
/** UI may be the host's child (normal launch) or parent (UI-first mode). */
inline pid_t analyzer_ui_pid() {
    const auto value = std::getenv("VST3_ANALYZER_UI_PID");
    return value ? static_cast<pid_t>(std::strtol(value, nullptr, 10)) : ::getppid();
}

/** Native editor appearance saved separately from the plugin's audio/controller preset. */
struct EditorState {
    bool visible = false; ///< Whether the native editor is currently shown.
    bool has_geometry = false; ///< Whether a native window has been created and sized.
    double x = 0; ///< Desktop window origin: logical Cocoa coordinates or physical X11 pixels.
    double y = 0; ///< Desktop window origin: logical Cocoa coordinates or physical X11 pixels.
    double width = 0; ///< Plugin content width excluding decorations: Cocoa points or X11 pixels.
    double height = 0; ///< Plugin content height excluding decorations: Cocoa points or X11 pixels.
};

/** Platform window owning an attached plugin view, isolated in the native host process. */
class NativeEditor {
public:
    /** Detach the view before releasing its native parent window. */
    virtual ~NativeEditor() = default;
    /** Bring the retained editor window to the front. */
    virtual void show() = 0;
    /** Raise an already visible window without activating its application or taking keyboard focus. */
    virtual void raise() = 0;
    /** Hide the window without losing its view state. */
    virtual void hide() = 0;
    /** Report actual visibility, including native close-button actions. */
    virtual bool visible() const = 0;
    /** Capture actual native position, content size and visibility. */
    virtual EditorState state() const = 0;
    /** Apply validated saved geometry without showing a hidden window.
     * @param state Validated saved native geometry to apply while preserving hidden-window visibility.
     */
    virtual void restore(const EditorState& state) = 0;
};
/** Return whether this build provides a native editor window backend. */
bool native_editor_supported();
/** Return whether the supplied view supports this backend's native embedding type.
 * @param view Optional SDK editor view to query without attaching or recreating it.
 */
bool native_editor_view_supported(Steinberg::IPlugView* view);
/** Attach the supplied view to a new platform window; retain a view reference.
 * @param view SDK view to attach; the window retains its own reference until detachment.
 * @param plugin_name UTF-8 name of the selected audio-effect class, used in the editor title.
 */
std::unique_ptr<NativeEditor> create_native_editor(Steinberg::IPlugView* view, const std::string& plugin_name);
/** Dispatch pending UI events without blocking the serialized audio/control thread. */
void pump_editor_events();
} // namespace analyzer
