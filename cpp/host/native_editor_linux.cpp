// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Optional X11/XEmbed VST3 editor windows with resize, visibility, geometry and Linux run-loop support.
 */
#include "host/native_editor.h"
#include "host/linux_run_loop.h"
#include "pluginterfaces/base/funknownimpl.h"
#include "pluginterfaces/gui/iplugview.h"
#include <algorithm>
#include <cstdint>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <unistd.h>
#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>

namespace analyzer {
namespace {
using namespace Steinberg;
class LinuxEditor;
LinuxEditor* current_editor = nullptr; ///< Sole host-owned editor receiving events on this display connection.

Display* host_display = nullptr; ///< Display whose X11 protocol errors are isolated from plugin-owned connections.
XErrorHandler previous_error_handler = nullptr; ///< Previous process-wide Xlib handler, preserved for other connections.
/** Keep stale native-window races from terminating the host through Xlib's default error handler.
 * @param display Connection that reported the asynchronous X11 error.
 * @param event Native X11 protocol error description.
 */
int editor_error(Display* display, XErrorEvent* event) {
    if (display != host_display && previous_error_handler) return previous_error_handler(display, event);
    if (event->error_code != BadWindow) {
        char message[256] {};
        XGetErrorText(display, event->error_code, message, sizeof(message));
        std::cerr << "X11 editor: " << message << '\n';
    }
    return 0;
}

/** Lazy X11 connection: headless measurements and unavailable displays remain supported. */
struct XConnection {
    Display* display = nullptr; ///< Host-owned display; nullptr disables native editors at runtime.
    /** Open X11 lazily and preserve the previous process-wide protocol-error handler. */
    XConnection() {
        // Plugins may use Xlib on their own toolkit threads even though host callbacks stay serialized.
        XInitThreads();
        display = XOpenDisplay(nullptr);
        if (display) {
            host_display = display;
            previous_error_handler = XSetErrorHandler(editor_error);
        }
    }
    /** Close the host's X11 connection after all editor and plugin resources are released. */
    ~XConnection() {
        if (display) {
            XCloseDisplay(display);
            XSetErrorHandler(previous_error_handler);
            host_display = nullptr;
        }
    }
};
/** Return the display connection used only on the native host main thread. */
Display* editor_display() {
    static XConnection connection;
    return connection.display;
}

/** Frame callback that delegates Linux registrations to the shared host-context run loop. */
class LinuxEditorFrame final : public U::Implements<U::Directly<IPlugFrame>> {
public:
    std::function<tresult(IPlugView*, ViewRect*)> resize; ///< Owning editor's synchronous resize callback.
    /** Supply the Linux run loop in addition to IPlugFrame.
     * @param iid Requested SDK interface identifier.
     * @param object Output reference-counted interface pointer.
     */
    tresult PLUGIN_API queryInterface(const TUID iid, void** object) override {
        if (FUnknownPrivate::iidEqual(iid, U::getTUID<Linux::IRunLoop>()))
            return linux_run_loop().queryInterface(iid, object);
        return U::Implements<U::Directly<IPlugFrame>>::queryInterface(iid, object);
    }
    /** Apply a plugin-requested parent resize and acknowledge its content dimensions.
     * @param view Requesting SDK view, checked by the owning editor.
     * @param rect Desired content rectangle in physical X11 pixels.
     */
    tresult PLUGIN_API resizeView(IPlugView* view, ViewRect* rect) override {
        return resize ? resize(view, rect) : kResultFalse;
    }
};

/** X11 parent and embedded view, both serialized with the service's control/audio work. */
class LinuxEditor final : public NativeEditor {
public:
    /** Create an XEmbed parent and attach a retained SDK view.
     * @param view SDK editor view supporting X11EmbedWindowID.
     * @param plugin_name UTF-8 effect name used in the native window title.
     */
    LinuxEditor(IPlugView* view, const std::string& plugin_name) : view_(view), display_(editor_display()) {
        if (!display_) throw std::runtime_error("No X11 display is available; use X11 or XWayland for plugin editors");
        if (current_editor) throw std::runtime_error("An editor window is already attached");
        if (view_->isPlatformTypeSupported(kPlatformTypeX11EmbedWindowID) != kResultOk)
            throw std::runtime_error("Plugin editor does not support X11/XEmbed");
        ViewRect rect;
        if (view_->getSize(&rect) != kResultOk || !valid_size(rect))
            throw std::runtime_error("Plugin editor returned invalid X11 dimensions");
        width_ = rect.getWidth();
        height_ = rect.getHeight();
        root_ = DefaultRootWindow(display_);
        delete_window_ = XInternAtom(display_, "WM_DELETE_WINDOW", False);
        protocols_ = XInternAtom(display_, "WM_PROTOCOLS", False);
        xembed_ = XInternAtom(display_, "_XEMBED", False);
        xembed_info_ = XInternAtom(display_, "_XEMBED_INFO", False);
        active_window_ = XInternAtom(display_, "_NET_ACTIVE_WINDOW", False);
        pid_atom_ = XInternAtom(display_, "_NET_WM_PID", False);
        leader_atom_ = XInternAtom(display_, "WM_CLIENT_LEADER", False);
        window_ = XCreateSimpleWindow(display_, root_, 80, 80, width_, height_, 0, 0, 0);
        if (!window_) throw std::runtime_error("Could not create X11 plugin editor window");
        try {
            // A dedicated child parent keeps plugin windows separate from window-manager decorations.
            embed_ = XCreateSimpleWindow(display_, window_, 0, 0, width_, height_, 0, 0, 0);
            XSelectInput(display_, window_, StructureNotifyMask | FocusChangeMask);
            XSelectInput(display_, embed_, SubstructureNotifyMask | StructureNotifyMask);
            XSelectInput(display_, root_, PropertyChangeMask);
            XSetWMProtocols(display_, window_, &delete_window_, 1);
            const auto title = plugin_name + " - Default Editor";
            XStoreName(display_, window_, title.c_str());
            const auto utf8 = XInternAtom(display_, "UTF8_STRING", False);
            const auto net_name = XInternAtom(display_, "_NET_WM_NAME", False);
            XChangeProperty(display_, window_, net_name, utf8, 8, PropModeReplace,
                            reinterpret_cast<const unsigned char*>(title.data()), title.size());
            const unsigned long pid = getpid();
            XChangeProperty(display_, window_, pid_atom_, XA_CARDINAL, 32, PropModeReplace,
                            reinterpret_cast<const unsigned char*>(&pid), 1);
            XClassHint hint {const_cast<char*>("vst3-plugin-analyzer-editor"), const_cast<char*>("VST3PluginAnalyzer")};
            XSetClassHint(display_, window_, &hint);
            update_size_hints();
            XMapWindow(display_, embed_);
            frame_ = owned(new LinuxEditorFrame);
            /** Forward SDK resize requests to the retained editor.
             * @param source SDK view requesting a resize.
             * @param size Requested content rectangle in physical pixels.
             */
            frame_->resize = [this](IPlugView* source, ViewRect* size) { return resize(source, size); };
            view_->setFrame(frame_);
            // Plugins use their own X11 connections. Ensure the server has created the
            // parent before another connection attempts to create children inside it.
            XSync(display_, False);
            attach_attempted_ = true;
            if (view_->attached(reinterpret_cast<void*>(static_cast<std::uintptr_t>(embed_)),
                                kPlatformTypeX11EmbedWindowID) != kResultOk)
                throw std::runtime_error("Could not attach plugin editor to its X11 parent");
            current_editor = this;
            track_activation();
            XFlush(display_);
        } catch (...) {
            // Constructor failures must detach before destroying the native parent.
            detach();
            XDestroyWindow(display_, window_);
            throw;
        }
    }
    /** Detach the view while its SDK frame, native parent and plugin code remain alive. */
    ~LinuxEditor() override {
        current_editor = nullptr;
        detach();
        XDestroyWindow(display_, window_);
        XFlush(display_);
    }
    /** Show the retained editor and ask the window manager to activate it. */
    void show() override {
        // The first F2 after a hidden restore has not gone through the automatic raise path.
        // Group before mapping so the window manager recognizes this as an application window.
        group_with_analyzer();
        XMapRaised(display_, window_);
        visible_ = true;
        // EWMH activation is a request; the desktop's focus-stealing policy remains authoritative.
        XEvent request {};
        request.xclient.type = ClientMessage;
        request.xclient.window = window_;
        request.xclient.message_type = active_window_;
        request.xclient.format = 32;
        request.xclient.data.l[0] = 1;
        request.xclient.data.l[1] = CurrentTime;
        XSendEvent(display_, root_, False, SubstructureRedirectMask | SubstructureNotifyMask, &request);
        XFlush(display_);
    }
    /** Raise an already visible editor without changing keyboard focus. */
    void raise() override {
        track_activation();
        if (!visible_ || (active_pid_ && active_pid_ != analyzer_ui_pid()) || previous_pid_ == getpid()) return;
        raise_window();
    }
    /** Hide the retained editor, preserving the embedded plugin view. */
    void hide() override {
        visible_ = false;
        XUnmapWindow(display_, window_);
        XFlush(display_);
    }
    /** Report visibility updated by host commands and native map/unmap notifications. */
    bool visible() const override { return visible_; }
    /** Capture client origin and content dimensions in physical X11 pixels. */
    EditorState state() const override {
        Window child;
        int x = 0, y = 0;
        XTranslateCoordinates(display_, window_, root_, 0, 0, &x, &y, &child);
        return {visible_, true, double(x), double(y), double(width_), double(height_)};
    }
    /** Restore saved geometry while keeping the title area on the current X11 screen.
     * @param state Validated saved visibility-independent client geometry.
     */
    void restore(const EditorState& state) override {
        if (!state.has_geometry) return;
        if (view_->canResize() == kResultTrue) {
            ViewRect rect(0, 0, static_cast<int32>(state.width), static_cast<int32>(state.height));
            view_->checkSizeConstraint(&rect);
            resize(view_, &rect);
        }
        // Screen bounds include multiple monitors on an ordinary X11/XWayland desktop.
        const int screen = DefaultScreen(display_);
        const auto x = std::clamp(static_cast<int>(state.x), 0, std::max(0, DisplayWidth(display_, screen) - 80));
        const auto y = std::clamp(static_cast<int>(state.y), 0, std::max(0, DisplayHeight(display_, screen) - 60));
        XMoveWindow(display_, window_, x, y);
        XFlush(display_);
    }
    /** Deliver one native event to this editor without blocking the IPC loop.
     * @param event X11 event from the host-owned display connection.
     */
    void dispatch(const XEvent& event) {
        if (event.type == PropertyNotify && event.xproperty.window == root_ && event.xproperty.atom == active_window_) {
            track_activation();
        } else if (event.type == ClientMessage && event.xclient.window == window_ &&
                   event.xclient.message_type == protocols_ && static_cast<Atom>(event.xclient.data.l[0]) == delete_window_) {
            hide();
        } else if ((event.type == CreateNotify && event.xcreatewindow.parent == embed_) ||
                   (event.type == ReparentNotify && event.xreparent.parent == embed_)) {
            // XEmbed clients may create directly under the parent or reparent an existing window.
            plugin_window_ = event.type == CreateNotify ? event.xcreatewindow.window : event.xreparent.window;
            XSelectInput(display_, plugin_window_, PropertyChangeMask | StructureNotifyMask);
            send_embed(0, 0, embed_, 0); // XEMBED_EMBEDDED_NOTIFY, protocol version zero.
            update_embed_mapping();
        } else if (event.type == DestroyNotify && event.xdestroywindow.window == plugin_window_) {
            plugin_window_ = 0;
        } else if (event.type == PropertyNotify && event.xproperty.window == plugin_window_ &&
                   event.xproperty.atom == xembed_info_) {
            update_embed_mapping();
        } else if (event.type == ClientMessage && event.xclient.window == embed_ && event.xclient.message_type == xembed_) {
            if (event.xclient.data.l[1] == 3) { // XEMBED_REQUEST_FOCUS.
                XWindowAttributes attributes {};
                if (XGetWindowAttributes(display_, embed_, &attributes) && attributes.map_state == IsViewable) {
                    XSetInputFocus(display_, embed_, RevertToParent, CurrentTime);
                    send_embed(4); // XEMBED_FOCUS_IN.
                }
            }
        } else if (event.xany.window == window_) {
            if (event.type == MapNotify) visible_ = true;
            else if (event.type == UnmapNotify) visible_ = false;
            else if (event.type == FocusIn || event.type == FocusOut) {
                const bool focused = event.type == FocusIn;
                view_->onFocus(focused);
                send_embed(focused ? 1 : 2); // XEMBED_WINDOW_ACTIVATE / DEACTIVATE.
                send_embed(focused ? 4 : 5); // XEMBED_FOCUS_IN / OUT.
            } else if (event.type == ConfigureNotify &&
                       (event.xconfigure.width != width_ || event.xconfigure.height != height_)) {
                // Honor fixed-size views and constrain interactive native resizes before acknowledging them.
                ViewRect rect(0, 0, event.xconfigure.width, event.xconfigure.height);
                if (view_->canResize() != kResultTrue) rect = ViewRect(0, 0, width_, height_);
                else view_->checkSizeConstraint(&rect);
                resize(view_, &rect);
            }
        }
    }
private:
    /** Ask the window manager to raise this application's editor without activating it. */
    void raise_window() {
        group_with_analyzer();
        XRaiseWindow(display_, window_);
        XFlush(display_);
    }
    /** Associate the two processes' windows so window managers permit an in-application raise. */
    void group_with_analyzer() {
        const auto active = property_word(root_, active_window_, XA_WINDOW);
        if (!active || property_word(active, pid_atom_, XA_CARDINAL) != static_cast<unsigned long>(analyzer_ui_pid())) return;
        auto leader = property_word(active, leader_atom_, XA_WINDOW);
        if (!leader) leader = active;
        if (leader == group_leader_) return;
        // Share window grouping, not transient ownership: the analyzer can still cover its editor.
        auto* hints = XGetWMHints(display_, window_);
        if (!hints) hints = XAllocWMHints();
        if (!hints) return;
        hints->flags |= WindowGroupHint;
        hints->window_group = leader;
        XSetWMHints(display_, window_, hints);
        XFree(hints);
        XChangeProperty(display_, window_, leader_atom_, XA_WINDOW, 32, PropModeReplace,
                        reinterpret_cast<const unsigned char*>(&leader), 1);
        group_leader_ = leader;
    }
    /** Validate the SDK rectangle before passing unsigned sizes into Xlib.
     * @param rect Requested content rectangle in physical pixels.
     */
    static bool valid_size(const ViewRect& rect) {
        return rect.getWidth() > 0 && rect.getHeight() > 0 && rect.getWidth() <= 16384 && rect.getHeight() <= 16384;
    }
    /** Detach an attempted attachment and break the SDK frame callback reference cycle. */
    void detach() {
        if (attach_attempted_) view_->removed();
        view_->setFrame(nullptr);
        if (frame_) frame_->resize = {};
    }
    /** Apply content dimensions and synchronously acknowledge plugin-initiated resizing.
     * @param source View requesting the resize, required to match the retained view.
     * @param rect Requested SDK rectangle, bounded before native resizing.
     */
    tresult resize(IPlugView* source, ViewRect* rect) {
        if (source != view_.get() || !rect || !valid_size(*rect)) return kInvalidArgument;
        width_ = rect->getWidth();
        height_ = rect->getHeight();
        XResizeWindow(display_, window_, width_, height_);
        XResizeWindow(display_, embed_, width_, height_);
        update_size_hints();
        XFlush(display_);
        return view_->onSize(rect);
    }
    /** Keep window-manager sizing hints aligned with fixed-size or resizable plugin views. */
    void update_size_hints() {
        XSizeHints hints {};
        hints.flags = PMinSize | PMaxSize;
        const bool resizable = view_->canResize() == kResultTrue;
        hints.min_width = resizable ? 1 : width_;
        hints.min_height = resizable ? 1 : height_;
        hints.max_width = resizable ? 16384 : width_;
        hints.max_height = resizable ? 16384 : height_;
        XSetWMNormalHints(display_, window_, &hints);
    }
    /** Honor the embedded client's XEMBED_MAPPED flag when its metadata becomes available. */
    void update_embed_mapping() {
        Atom actual = None;
        int format = 0;
        unsigned long count = 0, remaining = 0;
        unsigned char* data = nullptr;
        const auto status = XGetWindowProperty(display_, plugin_window_, xembed_info_, 0, 2, False, xembed_info_,
                                               &actual, &format, &count, &remaining, &data);
        if (status == Success && actual == xembed_info_ && format == 32 && count >= 2 && data) {
            const auto flags = reinterpret_cast<unsigned long*>(data)[1];
            if (flags & 1) XMapWindow(display_, plugin_window_);
            else XUnmapWindow(display_, plugin_window_);
        }
        if (data) XFree(data);
    }
    /** Send one XEmbed message to the plugin's current embedded window.
     * @param opcode XEmbed operation number.
     * @param detail Operation-specific detail field.
     * @param first First operation-specific data word.
     * @param second Second operation-specific data word.
     */
    void send_embed(long opcode, long detail = 0, long first = 0, long second = 0) {
        if (!plugin_window_) return;
        XEvent message {};
        message.xclient.type = ClientMessage;
        message.xclient.window = plugin_window_;
        message.xclient.message_type = xembed_;
        message.xclient.format = 32;
        message.xclient.data.l[0] = CurrentTime;
        message.xclient.data.l[1] = opcode;
        message.xclient.data.l[2] = detail;
        message.xclient.data.l[3] = first;
        message.xclient.data.l[4] = second;
        XSendEvent(display_, plugin_window_, False, NoEventMask, &message);
        XFlush(display_);
    }
    /** Read an unsigned X11 property without retaining Xlib-owned allocation.
     * @param window Native window whose property is requested.
     * @param property Atom naming a one-word window or cardinal property.
     * @param type Expected property type, preventing reinterpretation of arbitrary data.
     */
    unsigned long property_word(Window window, Atom property, Atom type) const {
        Atom actual = None;
        int format = 0;
        unsigned long count = 0, remaining = 0;
        unsigned char* data = nullptr;
        const auto status = XGetWindowProperty(display_, window, property, 0, 1, False, type,
                                               &actual, &format, &count, &remaining, &data);
        const auto value = status == Success && actual == type && format == 32 && count && data
                         ? *reinterpret_cast<unsigned long*>(data) : 0;
        if (data) XFree(data);
        return value;
    }
    /** Track X11 process activation so editor-to-analyzer switches need not raise the editor again. */
    void track_activation() {
        const auto active = property_word(root_, active_window_, XA_WINDOW);
        const auto pid = active ? static_cast<pid_t>(property_word(active, pid_atom_, XA_CARDINAL)) : 0;
        if (pid != active_pid_) {
            previous_pid_ = active_pid_;
            active_pid_ = pid;
            // Qt can announce activation before the root property changes. Handle that later
            // property notification here as well, rather than losing the early IPC request.
            if (visible_ && active_pid_ == analyzer_ui_pid() && previous_pid_ != getpid()) raise_window();
        }
    }
    IPtr<IPlugView> view_; ///< Retained plugin view, detached before module unloading.
    IPtr<LinuxEditorFrame> frame_; ///< SDK resize and Linux run-loop query callback.
    Display* display_ = nullptr; ///< Borrowed host display, used only from the native main thread.
    Window root_ = 0; ///< Desktop root for EWMH activation and geometry.
    Window window_ = 0; ///< Window-manager-owned top-level editor shell.
    Window embed_ = 0; ///< Host-owned XEmbed parent passed to the SDK view.
    Window plugin_window_ = 0; ///< Borrowed direct child created by the plugin.
    Atom delete_window_ = None; ///< Native close request, interpreted as hide.
    Atom protocols_ = None; ///< ICCCM protocol messages accepted by the editor shell.
    Atom xembed_info_ = None; ///< Embedded client protocol version and mapping flags.
    Atom xembed_ = None; ///< XEmbed protocol message type.
    Atom active_window_ = None; ///< EWMH foreground-window property and activation request type.
    Atom pid_atom_ = None; ///< EWMH process identifier property.
    Atom leader_atom_ = None; ///< ICCCM application-group leader property.
    Window group_leader_ = 0; ///< Analyzer's window-group leader shared across its Qt windows.
    int width_ = 0; ///< Current physical content width in pixels.
    int height_ = 0; ///< Current physical content height in pixels.
    bool visible_ = false; ///< Last commanded/native map state; closing retains the view.
    bool attach_attempted_ = false; ///< Whether removed() is needed, even after partial attachment failure.
    pid_t active_pid_ = 0; ///< Last foreground X11 process, where exposed by the window manager.
    pid_t previous_pid_ = 0; ///< Foreground process immediately preceding active_pid_.
};
} // namespace

/** Advertise editor support only when the X11 backend can connect to a display. */
bool native_editor_supported() { return editor_display() != nullptr; }
/** Check editor compatibility so Wayland-only views can still be analyzed without a native editor.
 * @param view Optional SDK editor view queried for X11EmbedWindowID support.
 */
bool native_editor_view_supported(Steinberg::IPlugView* view) {
    return view && view->isPlatformTypeSupported(Steinberg::kPlatformTypeX11EmbedWindowID) == Steinberg::kResultOk;
}
/** Create a retained X11 editor without adding a GUI toolkit to the native host.
 * @param view SDK editor view to attach using X11EmbedWindowID.
 * @param plugin_name UTF-8 selected effect name used in the window title.
 */
std::unique_ptr<NativeEditor> create_native_editor(Steinberg::IPlugView* view, const std::string& plugin_name) {
    return std::make_unique<LinuxEditor>(view, plugin_name);
}
/** Dispatch bounded X11 work and SDK timers/descriptors between serialized audio/control requests. */
void pump_editor_events() {
    // Native windows use a separate display connection from plugin-owned GUI toolkit event queues.
    auto* display = editor_display();
    if (display) {
        for (int count = 0; count < 64 && XPending(display); ++count) {
            XEvent event {};
            XNextEvent(display, &event);
            if (current_editor) current_editor->dispatch(event);
        }
    }
    pump_linux_run_loop();
}
} // namespace analyzer
