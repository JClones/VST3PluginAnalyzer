// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Cocoa implementation of VST3 editor windows, SDK resize callbacks, saved geometry and event dispatch.
 */
#include "host/native_editor.h"
#include "pluginterfaces/gui/iplugview.h"
#include "pluginterfaces/base/funknownimpl.h"
#import <Cocoa/Cocoa.h>
#include <functional>
#include <stdexcept>
#include <unistd.h>

namespace analyzer { class MacEditor; }
/** Cocoa delegate borrowing its owner; it never outlives the native editor. */
@interface AnalyzerEditorDelegate : NSObject <NSWindowDelegate>
@property(assign) analyzer::MacEditor* owner; ///< Non-owning editor callback target.
@end

namespace analyzer {
using namespace Steinberg;
/** SDK frame callback forwarding plugin-initiated resize requests to its parent window. */
struct EditorFrame : U::Implements<U::Directly<IPlugFrame>> {
    std::function<tresult(IPlugView*, ViewRect*)> resize; ///< Owning window's serialized resize callback.
    /** Resize the parent and acknowledge the resulting plugin view size synchronously.
     * @param view SDK view requesting a parent-window resize.
     * @param rect Requested logical content rectangle passed to the owning window.
     */
    tresult PLUGIN_API resizeView(IPlugView* view, ViewRect* rect) override {
        // Reject callbacks after the window has detached its view.
        return resize ? resize(view, rect) : kResultFalse;
    }
};

/** Cocoa window and SDK view lifetime, both used exclusively on the host main thread. */
class MacEditor final : public NativeEditor {
public:
    /** Allocate the parent window and attach the plugin's NSView representation.
     * @param view SDK view retained and attached to a Cocoa content view.
     * @param plugin_name UTF-8 name of the selected effect, displayed before the Default Editor suffix.
     */
    MacEditor(IPlugView* view, const std::string& plugin_name) : view_(view) {
        // Initialize Cocoa lazily; headless measurements do not create an application window.
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        if (view_->isPlatformTypeSupported(kPlatformTypeNSView) != kResultOk)
            throw std::runtime_error("Plugin editor does not support macOS NSView");
        ViewRect rect;
        if (view_->getSize(&rect) != kResultOk || rect.getWidth() < 1 || rect.getHeight() < 1)
            throw std::runtime_error("Plugin editor returned an invalid size");
        NSUInteger style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable;
        if (view_->canResize() == kResultTrue) style |= NSWindowStyleMaskResizable;
        window_ = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, rect.getWidth(), rect.getHeight())
                                            styleMask:style backing:NSBackingStoreBuffered defer:NO];
        window_.releasedWhenClosed = NO;
        const auto title = plugin_name + " - Default Editor";
        window_.title = [NSString stringWithUTF8String:title.c_str()];
        delegate_ = [AnalyzerEditorDelegate new];
        delegate_.owner = this;
        window_.delegate = delegate_;
        frame_ = owned(new EditorFrame);
        /** Forward a plugin resize to this window.
         * @param source SDK view requesting the resize; must match the retained view.
         * @param size Requested logical content rectangle passed to resize().
         */
        frame_->resize = [this](IPlugView* source, ViewRect* size) { return resize(source, size); };
        view_->setFrame(frame_);
        if (view_->attached((__bridge void*)window_.contentView, kPlatformTypeNSView) != kResultOk) {
            // Constructor failure still breaks the SDK reference cycle and closes the parent.
            view_->removed();
            view_->setFrame(nullptr);
            frame_->resize = {};
            window_.delegate = nil;
            [window_ close];
            throw std::runtime_error("Could not attach plugin editor");
        }
        attached_ = true;
        [window_ center];
        // Observe the workspace, since Qt cannot identify the application that previously owned focus.
        active_process_ = NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier;
        /** Track the process that became active and its predecessor.
         * @param notification Workspace activation notification containing the newly active NSRunningApplication.
         */
        activation_observer_ = [NSWorkspace.sharedWorkspace.notificationCenter
            addObserverForName:NSWorkspaceDidActivateApplicationNotification object:nil queue:nil
            usingBlock:^(NSNotification* notification) {
                // Record only real process transitions; duplicate notifications preserve the previous owner.
                NSRunningApplication* application = notification.userInfo[NSWorkspaceApplicationKey];
                if (application && application.processIdentifier != active_process_) {
                    previous_process_ = active_process_;
                    active_process_ = application.processIdentifier;
                }
            }];
    }
    /** Detach plugin UI while its module, frame and native parent are still alive. */
    ~MacEditor() override {
        // Unregister before destroying the C++ callback target captured by the notification block.
        if (activation_observer_)
            [NSWorkspace.sharedWorkspace.notificationCenter removeObserver:activation_observer_];
        // Cancel callbacks before closing the window and releasing the view.
        if (attached_) view_->removed();
        view_->setFrame(nullptr);
        frame_->resize = {};
        delegate_.owner = nullptr;
        window_.delegate = nil;
        [window_ close];
    }
    /** Present a previously hidden window and activate its application. */
    void show() override {
        // Showing never reconfigures audio or recreates the controller.
        [window_ makeKeyAndOrderFront:nil];
        [NSApp activateIgnoringOtherApps:YES];
    }
    /** Raise the visible editor while leaving keyboard focus with the analyzer. */
    void raise() override {
        // Drain activation notifications before evaluating an IPC request from the newly active Python parent.
        pump_editor_events();
        const auto foreground = NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier;
        if (foreground != analyzer_ui_pid()) return;
        // Switching from our editor to the analyzer must leave the analyzer in front.
        if (active_process_ == analyzer_ui_pid() && previous_process_ == getpid()) return;
        // The editor lives in a separate process; ordering alone must not activate that application.
        if (window_.visible) [window_ orderFrontRegardless];
    }
    /** Hide the editor while retaining all plugin view resources. */
    void hide() override {
        // The native close button uses this same non-destructive operation.
        [window_ orderOut:nil];
    }
    /** Query native visibility after either host commands or user window actions. */
    bool visible() const override {
        // Cocoa owns the definitive state, rather than a host-side toggle cache.
        return window_.visible;
    }
    /** Read actual window geometry even when the window is hidden. */
    EditorState state() const override {
        // Save content dimensions separately from window decorations and desktop position.
        const auto origin = window_.frame.origin;
        const auto size = window_.contentView.bounds.size;
        return {bool(window_.visible), true, origin.x, origin.y, size.width, size.height};
    }
    /** Restore saved geometry using the plugin's sizing constraints and current displays.
     * @param state Saved window origin and content dimensions; applied only when geometry is present.
     */
    void restore(const EditorState& state) override {
        if (!state.has_geometry) return;
        // Fixed-size plugin views retain their current preferred size after plugin updates.
        if (view_->canResize() == kResultTrue) {
            ViewRect rect(0, 0, state.width, state.height);
            view_->checkSizeConstraint(&rect);
            resize(view_, &rect);
        }
        [window_ setFrameOrigin:NSMakePoint(state.x, state.y)];
        // Keep a reachable title bar if the saved monitor is no longer connected.
        bool reachable = false;
        const auto frame = window_.frame;
        const auto title = NSMakeRect(frame.origin.x, NSMaxY(frame) - 24, frame.size.width, 24);
        for (NSScreen* screen in [NSScreen screens]) {
            const auto intersection = NSIntersectionRect(title, screen.visibleFrame);
            if (intersection.size.width >= 40 && intersection.size.height >= 12) reachable = true;
        }
        if (!reachable) [window_ center];
    }
    /** Apply a plugin-initiated size request and synchronously notify its view.
     * @param source Requesting view, which must match the attached plugin view.
     * @param size Requested logical content rectangle, validated before resizing and onSize notification.
     */
    tresult resize(IPlugView* source, ViewRect* size) {
        // Bound invalid plugin sizes before passing dimensions into Cocoa.
        if (source != view_.get() || !size || size->getWidth() < 1 || size->getHeight() < 1 ||
            size->getWidth() > 16384 || size->getHeight() > 16384) return kInvalidArgument;
        [window_ setContentSize:NSMakeSize(size->getWidth(), size->getHeight())];
        return view_->onSize(size);
    }
    /** Constrain a user resize to sizes accepted by the SDK view.
     * @param proposed Proposed Cocoa frame size including window decorations, in logical points.
     */
    NSSize constrain(NSSize proposed) {
        // Convert window-frame dimensions to the plugin's logical content coordinates.
        auto content = [window_ contentRectForFrameRect:NSMakeRect(0, 0, proposed.width, proposed.height)];
        ViewRect rect(0, 0, content.size.width, content.size.height);
        view_->checkSizeConstraint(&rect);
        return [window_ frameRectForContentRect:NSMakeRect(0, 0, rect.getWidth(), rect.getHeight())].size;
    }
    /** Notify the view after Cocoa finishes applying a user resize. */
    void resized() {
        // Keep SDK geometry synchronized without requesting another native resize.
        const auto size = window_.contentView.bounds.size;
        ViewRect rect(0, 0, size.width, size.height);
        view_->onSize(&rect);
    }
private:
    IPtr<IPlugView> view_; ///< Retained SDK view, detached before controller termination.
    IPtr<EditorFrame> frame_; ///< Host callback reference retained until SDK setFrame(nullptr).
    NSWindow* window_ = nil; ///< ARC-owned Cocoa parent window.
    AnalyzerEditorDelegate* delegate_ = nil; ///< ARC-owned resize/close callback delegate.
    bool attached_ = false; ///< Whether removed() is required during teardown.
    id activation_observer_ = nil; ///< Workspace observation token removed before editor destruction.
    pid_t active_process_ = 0; ///< Most recently activated application process observed by the workspace.
    pid_t previous_process_ = 0; ///< Application process active immediately before active_process_.
};

bool native_editor_supported() {
    // The Cocoa backend is compiled only on macOS.
    return true;
}
/** Check editor compatibility without attaching or creating another view.
 * @param view Optional retained SDK editor view to query for NSView support.
 */
bool native_editor_view_supported(IPlugView* view) {
    return view && view->isPlatformTypeSupported(kPlatformTypeNSView) == kResultOk;
}
/** Create the macOS native editor wrapper with unique window ownership.
 * @param view SDK view to retain and attach to a newly allocated Cocoa editor window.
 * @param plugin_name UTF-8 effect name included in the native window title.
 */
std::unique_ptr<NativeEditor> create_native_editor(IPlugView* view, const std::string& plugin_name) {
    // Unique ownership keeps window destruction ordered before plugin unloading.
    return std::make_unique<MacEditor>(view, plugin_name);
}
void pump_editor_events() {
    // Drain a bounded number of pending events so UI traffic cannot starve audio IPC.
    if (!NSApp) return;
    @autoreleasepool {
        for (int count = 0; count < 32; ++count) {
            NSEvent* event = [NSApp nextEventMatchingMask:NSEventMaskAny untilDate:[NSDate distantPast]
                                                 inMode:NSDefaultRunLoopMode dequeue:YES];
            if (!event) break;
            [NSApp sendEvent:event];
        }
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0, true);
        [NSApp updateWindows];
    }
}
} // namespace analyzer

@implementation AnalyzerEditorDelegate
/** Treat the native close button as hide, retaining the reusable plugin view.
 * @param sender Cocoa window whose close button was pressed; the owning editor is hidden instead.
 */
- (BOOL)windowShouldClose:(NSWindow*)sender {
    // Python observes visibility on its next polling cycle.
    if (self.owner) self.owner->hide();
    return NO;
}
/** Pass proposed user dimensions through the plugin's size constraints.
 * @param sender Cocoa window being resized.
 * @param size Proposed decorated frame size in logical points, constrained by the plugin view.
 */
- (NSSize)windowWillResize:(NSWindow*)sender toSize:(NSSize)size {
    // A detached delegate cannot access the released controller.
    return self.owner ? self.owner->constrain(size) : size;
}
/** Synchronize the plugin view after the Cocoa window changes size.
 * @param notification Cocoa resize notification; current content dimensions are read from the owned window.
 */
- (void)windowDidResize:(NSNotification*)notification {
    // Programmatic resize and user resize both require SDK onSize notification.
    if (self.owner) self.owner->resized();
}
@end
