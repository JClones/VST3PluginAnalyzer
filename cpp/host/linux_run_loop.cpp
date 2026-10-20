// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Lightweight VST3 Linux run loop with monotonic timers and nonblocking poll dispatch.
 */
#include "host/linux_run_loop.h"
#include "pluginterfaces/base/funknownimpl.h"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <poll.h>
#include <vector>

namespace analyzer {
namespace {
using namespace Steinberg;
using Clock = std::chrono::steady_clock;

/** Retained plugin callback and its readable descriptor. */
struct EventRegistration {
    IPtr<Linux::IEventHandler> handler; ///< Callback retained while registered or being dispatched.
    int fd; ///< Borrowed descriptor; its owner remains the plugin.
    std::uint64_t token; ///< Unique registration identity, protecting callback-driven replacement.
};
/** Retained plugin timer and its monotonic schedule. */
struct TimerRegistration {
    IPtr<Linux::ITimerHandler> handler; ///< Callback retained while registered or being dispatched.
    std::chrono::milliseconds interval; ///< Positive period between notifications.
    Clock::time_point next; ///< Earliest next notification, without catch-up bursts.
    std::uint64_t token; ///< Unique identity used when callbacks mutate registration lists.
};

/** Single-threaded callback registry shared by host context and editor frames. */
class LinuxRunLoop final : public U::ImplementsNonDestroyable<U::Directly<Linux::IRunLoop>> {
public:
    /** Register a retained handler for a borrowed readable fd.
     * @param handler Plugin callback receiving readiness notifications.
     * @param fd Plugin-owned descriptor, registered at most once.
     */
    tresult PLUGIN_API registerEventHandler(Linux::IEventHandler* handler, Linux::FileDescriptor fd) override {
        // Match the SDK contract without taking ownership of plugin file descriptors.
        if (!handler || fd < 0 || std::any_of(events_.begin(), events_.end(),
            /** @param entry Existing descriptor registration checked for duplication. */
            [fd](const auto& entry) { return entry.fd == fd; })) return kInvalidArgument;
        events_.push_back({handler, fd, ++token_});
        return kResultOk;
    }
    /** Remove every descriptor owned by a registered callback.
     * @param handler Plugin callback whose descriptor registrations are removed.
     */
    tresult PLUGIN_API unregisterEventHandler(Linux::IEventHandler* handler) override {
        if (!handler) return kInvalidArgument;
        const auto count = events_.size();
        events_.erase(std::remove_if(events_.begin(), events_.end(),
            /** @param entry Existing descriptor registration compared with the callback. */
            [handler](const auto& entry) { return entry.handler.get() == handler; }), events_.end());
        return events_.size() != count ? kResultOk : kResultFalse;
    }
    /** Register a retained repeating callback using the monotonic clock.
     * @param handler Plugin timer callback, registered at most once.
     * @param milliseconds Positive timer interval, bounded to avoid clock-duration overflow.
     */
    tresult PLUGIN_API registerTimer(Linux::ITimerHandler* handler, Linux::TimerInterval milliseconds) override {
        // Intervals up to roughly 49 days are ample for UI timers and safely representable on supported targets.
        if (!handler || !milliseconds || milliseconds > UINT32_MAX ||
            std::any_of(timers_.begin(), timers_.end(),
                /** @param entry Existing timer checked for duplicate callback registration. */
                [handler](const auto& entry) { return entry.handler.get() == handler; })) return kInvalidArgument;
        const auto interval = std::chrono::milliseconds(milliseconds);
        timers_.push_back({handler, interval, Clock::now() + interval, ++token_});
        return kResultOk;
    }
    /** Remove a repeating callback without invoking it again.
     * @param handler Plugin timer callback to unregister.
     */
    tresult PLUGIN_API unregisterTimer(Linux::ITimerHandler* handler) override {
        if (!handler) return kInvalidArgument;
        const auto count = timers_.size();
        timers_.erase(std::remove_if(timers_.begin(), timers_.end(),
            /** @param entry Existing timer registration compared with the callback. */
            [handler](const auto& entry) { return entry.handler.get() == handler; }), timers_.end());
        return timers_.size() != count ? kResultOk : kResultFalse;
    }
    /** Dispatch bounded ready callbacks, allowing callbacks to unregister themselves or one another. */
    void pump() {
        // Snapshots retain callbacks while allowing safe mutation of the live registries.
        const auto events = events_;
        std::vector<pollfd> descriptors;
        for (const auto& event : events) descriptors.push_back({event.fd, POLLIN, 0});
        int budget = 64;
        if (!descriptors.empty() && ::poll(descriptors.data(), descriptors.size(), 0) > 0) {
            for (std::size_t index = 0; index < events.size() && budget; ++index) {
                const auto& event = events[index];
                const auto active = std::any_of(events_.begin(), events_.end(),
                    /** @param entry Live registration checked against the retained snapshot token. */
                    [&event](const auto& entry) { return entry.token == event.token; });
                if (active && descriptors[index].revents & (POLLIN | POLLHUP | POLLERR)) {
                    --budget;
                    event.handler->onFDIsSet(event.fd);
                }
            }
        }
        // Notify overdue timers once, never replaying a backlog after slow plugin processing.
        const auto timers = timers_;
        const auto now = Clock::now();
        for (const auto& timer : timers) {
            if (!budget) break;
            const auto active = std::find_if(timers_.begin(), timers_.end(),
                /** @param entry Live timer registration checked against its snapshot identity. */
                [&timer](const auto& entry) { return entry.token == timer.token; });
            if (active != timers_.end() && active->next <= now) {
                active->next = now + active->interval;
                --budget;
                timer.handler->onTimer();
            }
        }
    }
    /** Release callbacks while their plugin module is still loaded. */
    void clear() {
        // Empty the live lists before releasing references: callback destructors may unregister themselves.
        auto events = std::move(events_);
        auto timers = std::move(timers_);
        events_.clear();
        timers_.clear();
    }
private:
    std::vector<EventRegistration> events_; ///< Current descriptor registrations, mutated only on the host main thread.
    std::vector<TimerRegistration> timers_; ///< Current timer registrations, serialized with audio/control work.
    std::uint64_t token_ = 0; ///< Monotonically increasing registration identity.
};

/** Return the non-destroyable SDK interface implementation with ordinary static storage lifetime. */
LinuxRunLoop& implementation() {
    static LinuxRunLoop loop;
    return loop;
}
} // namespace

/** Return the shared run loop exposed through host context and IPlugFrame. */
Steinberg::Linux::IRunLoop& linux_run_loop() { return implementation(); }
/** Dispatch plugin callbacks without blocking the service's audio/control loop. */
void pump_linux_run_loop() { implementation().pump(); }
/** Release plugin callbacks before their native module is unloaded. */
void clear_linux_run_loop() { implementation().clear(); }
} // namespace analyzer
