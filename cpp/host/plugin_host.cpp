// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief SDK-backed plugin lifecycle, multi-bus audio routing, precision conversion, parameter delivery and state management.
 */
#include "host/plugin_host.h"
#include "host/native_editor.h"
#if defined(__linux__)
#include "host/linux_run_loop.h"
#endif
#include "pluginterfaces/gui/iplugview.h"

#include "public.sdk/source/vst/hosting/hostclasses.h"
#include "public.sdk/source/vst/hosting/plugprovider.h"
#include "public.sdk/source/vst/hosting/processdata.h"
#include "public.sdk/source/vst/hosting/parameterchanges.h"
#include "public.sdk/source/vst/hosting/eventlist.h"
#include "public.sdk/source/common/memorystream.h"
#include "public.sdk/source/vst/vstpresetfile.h"
#include "public.sdk/source/vst/utility/stringconvert.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstunits.h"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <map>
#include <stdexcept>
#include <chrono>
#include <random>

namespace analyzer {
using namespace Steinberg;
using namespace Steinberg::Vst;
namespace {
/** Require a successful SDK call and attach the failing operation to the error.
 * @param result SDK return code to require as kResultOk.
 * @param operation Operation name included in the exception if the SDK call failed.
 */
void check(tresult result, const char* operation) {
    // VST3 uses zero for both kResultOk and kResultTrue.
    if (result != kResultOk)
        throw std::runtime_error(std::string(operation) + " failed (VST3 result " +
                                 std::to_string(result) + ")");
}

/** Notify the optional processing-state hook used by SDK AudioEffect subclasses.
 * @param processor Initialized processor whose processing state is changed.
 * @param enabled True starts processing; false stops it.
 */
void notify_processing(IAudioProcessor& processor, bool enabled) {
    // The SDK base implementation returns kNotImplemented when no hook is needed.
    const auto result = processor.setProcessing(enabled);
    if (result != kNotImplemented) check(result, enabled ? "start processing" : "stop processing");
}

/** Load an SDK module and preserve the native loader's diagnostic on failure.
 * @param path Filesystem path to the VST3 module bundle loaded by the SDK.
 */
VST3::Hosting::Module::Ptr open_module(const std::string& path) {
    // Keep platform-specific module loading inside the SDK implementation.
    std::string error;
    auto module = VST3::Hosting::Module::create(path, error);
    if (!module) throw std::runtime_error("Cannot load module: " + error);
    return module;
}

/** Serialized controller/unit callbacks shared by headless measurements and native editors. */
struct Handler : U::Implements<U::Directly<IComponentHandler, IUnitHandler>> {
    std::map<ParamID, ParamValue> pending; ///< Latest edit per parameter, delivered next block.
    int32 restart_flags = 0; ///< Deferred plugin requests, checked outside callbacks.

    /** Acknowledge the start of an edit gesture.
     * @param id Parameter starting an edit gesture; unused because no automation is recorded.
     */
    tresult PLUGIN_API beginEdit(ParamID id) override {
        // No automation recording exists in the probe.
        return kResultOk;
    }
    /** Queue a controller-originated edit for processor delivery.
     * @param id Stable ID of the parameter being edited.
     * @param value Finite normalized value queued for the next processor block.
     */
    tresult PLUGIN_API performEdit(ParamID id, ParamValue value) override {
        // Reject invalid normalized values before touching the pending queue.
        if (!std::isfinite(value) || value < 0 || value > 1) return kInvalidArgument;
        pending[id] = value;
        return kResultOk;
    }
    /** Acknowledge the end of an edit gesture.
     * @param id Parameter ending an edit gesture; its value is already in the pending queue.
     */
    tresult PLUGIN_API endEdit(ParamID id) override {
        // Edits already reside in the pending queue.
        return kResultOk;
    }
    /** Acknowledge unit changes; subsequent menu queries read current controller state.
     * @param unit_id Newly selected SDK unit ID; routing is unchanged by this notification.
     */
    tresult PLUGIN_API notifyUnitSelection(UnitID unit_id) override {
        // Units are metadata, independent of the analyzer's fixed stereo routing.
        return kResultOk;
    }
    /** Acknowledge factory program list changes for the worker's next polling cycle.
     * @param list_id Changed SDK program list ID, refreshed by subsequent queries.
     * @param program_index Changed zero-based program index, or the SDK all-programs sentinel.
     */
    tresult PLUGIN_API notifyProgramListChange(ProgramListID list_id, int32 program_index) override {
        // Program names and selected checks are queried periodically instead of cached natively.
        return kResultOk;
    }
    /** Record reconfiguration requests without reentering the plugin.
     * @param flags Bitmask of SDK RestartFlags to handle outside the controller callback.
     */
    tresult PLUGIN_API restartComponent(int32 flags) override {
        // The probe detects unsupported changes before the next process call.
        restart_flags |= flags;
        return kResultOk;
    }
};
} // namespace

#if defined(__linux__)
/** SDK host application exposing Linux callbacks even for plugins without an editor. */
class AnalyzerHostApplication final : public HostApplication {
public:
    /** Provide IRunLoop in addition to the standard SDK host interfaces.
     * @param iid Requested SDK interface identifier.
     * @param object Output interface pointer, reference-counted according to the SDK contract.
     */
    tresult PLUGIN_API queryInterface(const TUID iid, void** object) override {
        // Let the run loop own its reference semantics; all other queries remain SDK behavior.
        if (FUnknownPrivate::iidEqual(iid, U::getTUID<Linux::IRunLoop>()))
            return linux_run_loop().queryInterface(iid, object);
        return HostApplication::queryInterface(iid, object);
    }
};
#else
using AnalyzerHostApplication = HostApplication;
#endif

/** Lifecycle state; member order keeps the host context and module alive longest. */
struct PluginHost::Impl {
    AnalyzerHostApplication application; ///< SDK host context, valid through termination.
    VST3::Hosting::Module::Ptr module; ///< Native module owning all plugin code.
    std::unique_ptr<PlugProvider> provider; ///< SDK component/controller lifecycle helper.
    IPtr<IComponent> component; ///< Initialized effect component.
    IPtr<IEditController> controller; ///< Optional parameter controller.
    IPtr<IAudioProcessor> processor; ///< Processing interface on the component.
    PrecisionSupport precision {false, false}; ///< Capabilities queried only while the instance is initialized/inactive.
    IPtr<IPlugView> editor_view; ///< Single retained editor view, reused by availability checks and native windows.
    bool editor_queried = false; ///< Whether optional editor availability has been queried for this instance.
    bool buses_configured = false; ///< Bus arrangements and activation were negotiated for this instance.
    IPtr<Handler> handler = owned(new Handler); ///< Controller callback lifetime owner.
    HostProcessData data; ///< SDK-owned per-bus audio buffers.
    ParameterChanges input_changes; ///< Parameter changes for one process block.
    ParameterChanges output_changes; ///< Processor-originated parameter feedback.
    EventList input_events; ///< Empty MIDI/event input for ordinary audio effects.
    EventList output_events; ///< Events emitted by the effect; audio measurements do not consume them.
    double sample_rate = 0; ///< Configured sample rate in Hz.
    int32 block_size = 0; ///< Maximum number of samples passed to process().
    int32 sample_size = kSample32; ///< Negotiated native plugin buffer precision.
    int32 channel_count = 0; ///< Equal main input/output channel count.
    int32 main_input = 0; ///< First advertised main input bus, driven by test audio.
    int32 main_output = 0; ///< First advertised main output bus, returned for analysis.
    bool active = false; ///< Whether setActive(true) succeeded.
    bool processing = false; ///< Whether setProcessing(true) succeeded.
    std::unique_ptr<NativeEditor> editor; ///< Native window detached before controller termination.
};

PluginHost::PluginHost() : impl_(std::make_unique<Impl>()) {
    // Keep SDK diagnostics separate from probe output.
    PlugProvider::setErrorStream(&std::cerr);
}

PluginHost::~PluginHost() {
    // Explicitly terminate before Impl's dependent members are destroyed.
    unload();
}

/** Inspect audio-effect classes without constructing a processor.
 * @param path Filesystem path to the VST3 module bundle to inspect.
 */
std::vector<PluginClass> PluginHost::inspect(const std::string& path) {
    // Return only processor classes; controller/test classes are not loadable effects.
    auto module = open_module(path);
    std::vector<PluginClass> result;
    for (const auto& info : module->getFactory().classInfos()) {
        if (info.category() == kVstAudioEffectClass)
            result.push_back({info.ID().toString(), info.name(), info.vendor(), info.version()});
    }
    return result;
}

/** Load and initialize one effect, replacing any previous instance.
 * @param path Filesystem path to the VST3 module bundle to load.
 * @param class_id Hexadecimal audio-effect class ID; empty selects the sole effect class.
 */
void PluginHost::load(const std::string& path, const std::string& class_id) {
    // Replace any existing instance with a fresh processor and controller.
    unload();
    auto& s = *impl_;
    try {
        s.module = open_module(path);
        std::vector<VST3::Hosting::ClassInfo> effects;
        for (const auto& info : s.module->getFactory().classInfos())
            if (info.category() == kVstAudioEffectClass &&
                (class_id.empty() || info.ID().toString() == class_id)) effects.push_back(info);
        if (effects.size() != 1)
            throw std::runtime_error("Select exactly one audio-effect class using --class ID");

        // Supply the context to both the factory and SDK initialization helper.
        s.module->getFactory().setHostContext(&s.application);
        PluginContextFactory::instance().setPluginContext(&s.application);
        s.provider = std::make_unique<PlugProvider>(s.module->getFactory(), effects.front());
        const bool initialized = s.provider->initialize();
        PluginContextFactory::instance().setPluginContext(nullptr);
        if (!initialized) throw std::runtime_error("Plugin initialization failed");
        s.component = s.provider->getComponentPtr();
        s.controller = s.provider->getControllerPtr();
        s.processor = U::cast<IAudioProcessor>(s.component);
        if (!s.processor) throw std::runtime_error("Plugin has no audio processor");
        s.precision = {s.processor->canProcessSampleSize(kSample32) == kResultOk,
                       s.processor->canProcessSampleSize(kSample64) == kResultOk};

        // Synchronize the controller to actual processor state, rather than assumed defaults.
        if (s.controller) {
            check(s.controller->setComponentHandler(s.handler), "setComponentHandler");
            MemoryStream state;
            if (s.component->getState(&state) == kResultOk) {
                check(state.seek(0, IBStream::kIBSeekSet, nullptr), "state seek");
                const auto result = s.controller->setComponentState(&state);
                if (result != kResultOk && result != kNotImplemented)
                    check(result, "setComponentState");
            }
        }
    } catch (...) {
        // Roll back partial setup while retaining the original diagnostic.
        PluginContextFactory::instance().setPluginContext(nullptr);
        unload();
        throw;
    }
}

void PluginHost::unload() noexcept {
    // Reverse the processing lifecycle before releasing interfaces and native code.
    auto& s = *impl_;
    s.editor.reset();
    s.editor_view.reset();
    s.editor_queried = s.buses_configured = false;
    s.precision = {false, false};
    if (s.processing) s.processor->setProcessing(false);
    s.processing = false;
    if (s.active) s.component->setActive(false);
    s.active = false;
    s.data.unprepare();
    if (s.controller) s.controller->setComponentHandler(nullptr);
    s.processor.reset();
    s.controller.reset();
    s.component.reset();
    s.provider.reset();
#if defined(__linux__)
    // Release any callbacks left by the terminated plugin before unloading its implementation code.
    clear_linux_run_loop();
#endif
    s.module.reset();

    // Discard all state belonging to the old instance.
    s.handler->pending.clear();
    s.handler->restart_flags = 0;
    s.input_changes.clearQueue();
    s.output_changes.clearQueue();
    s.channel_count = s.block_size = 0;
    s.sample_rate = 0;
}

/** Prepare all audio buses and activate the requested processing configuration.
 * @param sample_rate Processing sample rate in Hz, from 8000 through 768000.
 * @param block_size Maximum samples per native processing block, from 1 through 65536.
 * @param precision Native buffer preference: auto, float32 or float64.
 * @param processing_mode SDK processing mode: offline or realtime; pacing remains the caller's responsibility.
 */
void PluginHost::configure(double sample_rate, int32 block_size, const std::string& precision,
                           const std::string& processing_mode) {
    auto& s = *impl_;
    // Validate settings before disturbing an already configured instance.
    if (!s.processor) throw std::runtime_error("Load a plugin first");
    if (!std::isfinite(sample_rate) || sample_rate < 8000 || sample_rate > 768000 ||
        block_size < 1 || block_size > 65536)
        throw std::invalid_argument("Invalid sample rate or block size");
    if (s.component->getBusCount(kAudio, kInput) < 1 || s.component->getBusCount(kAudio, kOutput) < 1)
        throw std::runtime_error("Plugin requires an audio input and output bus");
    if (processing_mode != "offline" && processing_mode != "realtime")
        throw std::invalid_argument("Processing mode must be offline or realtime");
    const auto mode = processing_mode == "realtime" ? kRealtime : kOffline;
    const auto support = precision_support();
    int32 sample_size;
    if (precision == "auto") {
        if (!support.float32 && !support.float64) throw std::runtime_error("No supported audio precision");
        sample_size = support.float64 ? kSample64 : kSample32;
    } else if (precision == "float64" && support.float64) {
        sample_size = kSample64;
    } else if (precision == "float32" && support.float32) {
        sample_size = kSample32;
    } else {
        throw std::invalid_argument("Requested precision is invalid or unsupported by this plugin");
    }

    // Reconfiguration is permitted only with processing stopped and component inactive.
    if (s.processing) notify_processing(*s.processor, false);
    s.processing = false;
    if (s.active) check(s.component->setActive(false), "deactivate");
    s.active = false;
    s.block_size = s.channel_count = 0;
    s.data.unprepare();
    try {
        // Preserve every advertised arrangement so optional sidechain buses retain valid buffers.
        std::vector<SpeakerArrangement> inputs(s.component->getBusCount(kAudio, kInput));
        std::vector<SpeakerArrangement> outputs(s.component->getBusCount(kAudio, kOutput));
        s.main_input = s.main_output = 0;
        for (const auto direction : {kInput, kOutput}) {
            auto& arrangements = direction == kInput ? inputs : outputs;
            auto& main = direction == kInput ? s.main_input : s.main_output;
            bool found_main = false;
            for (int32 bus = 0; bus < static_cast<int32>(arrangements.size()); ++bus) {
                BusInfo info {};
                check(s.component->getBusInfo(kAudio, direction, bus, info), "audio bus info");
                check(s.processor->getBusArrangement(direction, bus, arrangements[bus]), "bus arrangement");
                if (!found_main && info.busType == kMain) {
                    main = bus;
                    found_main = true;
                }
            }
        }
        if (!s.buses_configured) {
            // A sample-rate/block-size change is not a request to renegotiate unchanged speaker layouts.
            check(s.processor->setBusArrangements(inputs.data(), static_cast<int32>(inputs.size()),
                                                  outputs.data(), static_cast<int32>(outputs.size())), "setBusArrangements");
        }
        BusInfo input {}, output {};
        check(s.component->getBusInfo(kAudio, kInput, s.main_input, input), "main input bus info");
        check(s.component->getBusInfo(kAudio, kOutput, s.main_output, output), "main output bus info");
        if ((input.channelCount != 1 && input.channelCount != 2) || input.channelCount != output.channelCount)
            throw std::runtime_error("Main input/output buses must have matching mono/stereo channels");
        // setupProcessing establishes Setup Done before bus activation and component activation.
        ProcessSetup setup {mode, sample_size, block_size, sample_rate};
        check(s.processor->setupProcessing(setup), "setupProcessing");
        if (!s.buses_configured) {
            // Existing bus activations survive a sample-rate change and must not be replayed unnecessarily.
            for (const auto direction : {kInput, kOutput}) {
                const auto count = s.component->getBusCount(kAudio, direction);
                for (int32 bus = 0; bus < count; ++bus)
                    check(s.component->activateBus(kAudio, direction, bus, true), "activate audio bus");
            }
            s.buses_configured = true;
        }

        // Rebuild bounded host-owned buffers without replacing the processor, controller or editor view.
        if (!s.data.prepare(*s.component, block_size, sample_size))
            throw std::runtime_error("Could not allocate processing buffers");
        s.data.processMode = mode;
        s.data.inputParameterChanges = &s.input_changes;
        s.data.outputParameterChanges = &s.output_changes;
        s.data.inputEvents = &s.input_events;
        s.data.outputEvents = &s.output_events;
        check(s.component->setActive(true), "activate component");
        s.active = true;
        notify_processing(*s.processor, true);
        s.processing = true;
        s.sample_size = sample_size;
        s.sample_rate = sample_rate;
        s.block_size = block_size;
        s.channel_count = input.channelCount;
        s.handler->restart_flags = 0;
    } catch (...) {
        // Leave a failed configuration loaded but inactive and safe to retry.
        if (s.active) s.component->setActive(false);
        s.active = false;
        s.data.unprepare();
        throw;
    }
}

std::vector<Bus> PluginHost::buses() const {
    // Report all buses; audio transport carries only the first main input/output pair.
    const auto& s = *impl_;
    if (!s.component) throw std::runtime_error("Load a plugin first");
    std::vector<Bus> result;
    for (const auto media : {kAudio, kEvent}) {
        for (const auto direction : {kInput, kOutput}) {
            for (int32 i = 0; i < s.component->getBusCount(media, direction); ++i) {
                BusInfo info {};
                check(s.component->getBusInfo(media, direction, i, info), "getBusInfo");
                result.push_back({StringConvert::convert(info.name, 128), direction == kInput,
                    media == kAudio, info.channelCount, info.busType == kMain, info.flags});
            }
        }
    }
    return result;
}

std::vector<Parameter> PluginHost::parameters() const {
    // A plugin without a controller has no editable parameter list.
    const auto& s = *impl_;
    if (!s.component) throw std::runtime_error("Load a plugin first");
    std::vector<Parameter> result;
    if (!s.controller) return result;
    for (int32 i = 0; i < s.controller->getParameterCount(); ++i) {
        ParameterInfo info {};
        check(s.controller->getParameterInfo(i, info), "getParameterInfo");
        const auto value = s.controller->getParamNormalized(info.id);
        String128 display {};
        s.controller->getParamStringByValue(info.id, value, display);
        result.push_back({info.id, StringConvert::convert(info.title, 128),
            StringConvert::convert(info.units, 128), StringConvert::convert(display, 128),
            value, info.defaultNormalizedValue, info.stepCount, info.flags});
    }
    return result;
}

bool PluginHost::has_editor() const {
    // Some controllers own one shared UI backend: creating a throwaway view can blank the open editor.
    auto& s = *impl_;
    if (!s.controller || !native_editor_supported()) return false;
    if (!s.editor_queried) {
        s.editor_view = owned(s.controller->createView(ViewType::kEditor));
        s.editor_queried = true;
    }
    return native_editor_view_supported(s.editor_view);
}

/** Show or hide the plugin editor without changing audio processing.
 * @param visible True shows or raises the native editor; false hides an existing window.
 */
void PluginHost::show_editor(bool visible) {
    // A retained hidden window avoids losing UI state on each toggle.
    auto& s = *impl_;
    if (!visible) {
        if (s.editor) s.editor->hide();
        return;
    }
    if (!s.editor) {
        if (!s.controller) throw std::runtime_error("Plugin has no edit controller");
        if (!has_editor()) throw std::runtime_error("Plugin has no default editor");
        s.editor = create_native_editor(s.editor_view, s.provider->getClassInfo().name());
    }
    s.editor->show();
}

/** Raise only an existing visible editor; never allocate or attach a view here. */
void PluginHost::raise_editor() {
    // Check authoritative native visibility so a delayed request cannot reopen a user-closed editor.
    if (impl_->editor && impl_->editor->visible()) impl_->editor->raise();
}

bool PluginHost::editor_visible() const {
    // Native close-button actions remain observable through this state query.
    return impl_->editor && impl_->editor->visible();
}

EditorState PluginHost::editor_state() const {
    // Query native geometry at capture time, rather than trusting the Python polling cache.
    return impl_->editor ? impl_->editor->state() : EditorState {};
}

/** Restore editor appearance without briefly showing a saved hidden window.
 * @param state Saved visibility and optional native position/content size to restore.
 */
void PluginHost::restore_editor(const EditorState& state) {
    // A saved hidden editor must be restored without briefly showing its window.
    auto& s = *impl_;
    if (!s.editor && (state.visible || state.has_geometry)) {
        if (!s.controller) throw std::runtime_error("Plugin has no edit controller");
        if (!has_editor()) throw std::runtime_error("Plugin has no default editor");
        s.editor = create_native_editor(s.editor_view, s.provider->getClassInfo().name());
    }
    if (s.editor) {
        s.editor->restore(state);
        if (state.visible) s.editor->show();
        else s.editor->hide();
    }
}

std::vector<Program> PluginHost::programs() const {
    // Program lists are optional; only expose programs that have an addressable change parameter.
    const auto& s = *impl_;
    std::vector<Program> result;
    if (!s.controller) return result;
    auto units = U::cast<IUnitInfo>(s.controller);
    for (int32 position = 0; position < s.controller->getParameterCount(); ++position) {
        ParameterInfo parameter {};
        if (s.controller->getParameterInfo(position, parameter) != kResultOk ||
            !(parameter.flags & ParameterInfo::kIsProgramChange) ||
            (parameter.flags & ParameterInfo::kIsReadOnly) || parameter.stepCount < 0) continue;
        ProgramListID list_id = kNoProgramListId;
        if (units) {
            // A program parameter belongs to the unit whose program list supplies its names.
            for (int32 unit_index = 0; unit_index < units->getUnitCount(); ++unit_index) {
                UnitInfo unit {};
                if (units->getUnitInfo(unit_index, unit) == kResultOk && unit.id == parameter.unitId)
                    list_id = unit.programListId;
            }
        }
        const auto current = static_cast<int32>(std::lround(
            s.controller->getParamNormalized(parameter.id) * parameter.stepCount));
        const auto prefix = parameter.unitId == kRootUnitId ? std::string() :
                            StringConvert::convert(parameter.title, 128) + " / ";
        for (int32 index = 0; index <= parameter.stepCount; ++index) {
            String128 name {};
            const auto value = parameter.stepCount ? double(index) / parameter.stepCount : 0.0;
            if (!units || list_id == kNoProgramListId ||
                units->getProgramName(list_id, index, name) != kResultOk)
                s.controller->getParamStringByValue(parameter.id, value, name);
            auto label = StringConvert::convert(name, 128);
            if (label.empty()) label = "Program " + std::to_string(index + 1);
            result.push_back({parameter.id, index, prefix + label, index == current});
        }
    }
    return result;
}

/** Queue a factory-program selection for the next processing block.
 * @param parameter_id Stable SDK program-change parameter ID identifying the program list.
 * @param index Zero-based program index within that parameter's discrete range.
 */
void PluginHost::select_program(std::uint32_t parameter_id, std::int32_t index) {
    // Validate the requested discrete index, then queue it like a normal real-world parameter edit.
    const auto list = parameters();
    const auto found = std::find_if(list.begin(), list.end(),
        /** @param item Parameter descriptor examined for the requested factory-program ID. */
        [parameter_id](const Parameter& item) { return item.id == parameter_id; });
    if (found == list.end() || !(found->flags & ParameterInfo::kIsProgramChange) ||
        index < 0 || index > found->steps) throw std::invalid_argument("Invalid factory program");
    set_parameter(parameter_id, found->steps ? double(index) / found->steps : 0.0);
}

/** Update the controller and queue its resulting value for the next audio block.
 * @param id Stable SDK ID of a writable controller parameter.
 * @param value Finite normalized parameter value in the inclusive range [0, 1].
 */
void PluginHost::set_parameter(std::uint32_t id, double value) {
    // Confirm ID, mutability and normalized range before calling the controller.
    auto& s = *impl_;
    if (!std::isfinite(value) || value < 0 || value > 1)
        throw std::invalid_argument("Parameter value must be finite and within [0, 1]");
    const auto list = parameters();
    const auto found = std::find_if(list.begin(), list.end(),
        /** @param parameter Controller descriptor examined for the requested stable parameter ID. */
        [id](const Parameter& parameter) { return parameter.id == id; }); // Match stable ID.
    if (found == list.end()) throw std::invalid_argument("Unknown parameter ID");
    if (found->flags & ParameterInfo::kIsReadOnly)
        throw std::invalid_argument("Parameter is read-only");

    // Controller updates do not automatically reach the audio processor.
    check(s.controller->setParamNormalized(id, value), "setParamNormalized");
    check(s.handler->performEdit(id, s.controller->getParamNormalized(id)), "queue parameter");
}

/** Parse a logical parameter string through the plugin controller and deliver it normally.
 * @param id Stable SDK ID of a writable controller parameter.
 * @param text UTF-8 logical value text accepted by the plugin.
 * @return Actual normalized controller value after the edit.
 */
double PluginHost::set_parameter_text(std::uint32_t id, const std::string& text) {
    // Require a loaded controller before converting or interpreting user-supplied text.
    auto& s = *impl_;
    if (!s.component || !s.controller) throw std::invalid_argument("Load a plugin with a controller first");
    const auto list = parameters();
    const auto found = std::find_if(list.begin(), list.end(),
        /** @param parameter Controller descriptor checked for the requested stable ID. */
        [id](const Parameter& parameter) { return parameter.id == id; });
    if (found == list.end()) throw std::invalid_argument("Unknown parameter ID");
    if (found->flags & ParameterInfo::kIsReadOnly) throw std::invalid_argument("Parameter is read-only");

    // The SDK string buffer is bounded; reject overlong input rather than silently truncating it.
    String128 value_text {};
    if (!StringConvert::convert(text, value_text)) throw std::invalid_argument("Parameter text is too long or invalid UTF-8");
    ParamValue normalized = 0.0;
    check(s.controller->getParamValueByString(id, value_text, normalized), "getParamValueByString");
    set_parameter(id, normalized);
    return s.controller->getParamNormalized(id);
}

/** Process consecutive audio while silencing auxiliary inputs and returning the main output.
 * @param input Channel-major float64 samples for the main input; channels must have equal lengths.
 * @param processing_ms Optional output receiving summed native callback time; nullptr disables clock reads.
 */
Audio PluginHost::process(const Audio& input, double* processing_ms) {
    auto& s = *impl_;
    // Validate layout and defer unsupported dynamic reconfiguration to the caller.
    if (!s.processing) throw std::runtime_error("Configure processing first");
    if (input.size() != static_cast<std::size_t>(s.channel_count))
        throw std::invalid_argument("Input channel count does not match plugin");
    const auto samples = input.front().size();
    for (const auto& channel : input)
        if (channel.size() != samples) throw std::invalid_argument("Unequal channel lengths");
    const int32 supported_flags = kLatencyChanged | kParamTitlesChanged | kParamValuesChanged | kMidiCCAssignmentChanged;
    if (s.handler->restart_flags & ~supported_flags)
        throw std::runtime_error("Plugin requested reconfiguration; reload before processing");
    s.handler->restart_flags &= ~supported_flags;
    Audio output(s.channel_count, std::vector<double>(samples));
    if (processing_ms) *processing_ms = 0.0;

    // Copy channel-major audio into bounded SDK buffers and process consecutive blocks.
    for (std::size_t offset = 0; offset < samples; offset += s.block_size) {
        if (s.handler->restart_flags & ~supported_flags)
            throw std::runtime_error("Plugin requested reconfiguration; reload before processing");
        const auto count = static_cast<int32>(std::min<std::size_t>(s.block_size, samples - offset));
        s.data.numSamples = count;
        // Clear every bus on every block, including sidechains a plugin may have touched previously.
        for (const auto direction : {kInput, kOutput}) {
            auto* buses = direction == kInput ? s.data.inputs : s.data.outputs;
            const auto bus_count = direction == kInput ? s.data.numInputs : s.data.numOutputs;
            for (int32 bus = 0; bus < bus_count; ++bus) {
                auto& buffers = buses[bus];
                buffers.silenceFlags = direction == kInput ? HostProcessData::kAllChannelsSilent : 0;
                for (int32 channel = 0; channel < buffers.numChannels; ++channel) {
                    if (s.sample_size == kSample64)
                        std::fill_n(buffers.channelBuffers64[channel], count, 0.0);
                    else
                        std::fill_n(buffers.channelBuffers32[channel], count, 0.f);
                }
            }
        }
        auto& main_input = s.data.inputs[s.main_input];
        main_input.silenceFlags = 0;
        for (int32 channel = 0; channel < s.channel_count; ++channel) {
            if (s.sample_size == kSample64)
                std::copy_n(input[channel].data() + offset, count, main_input.channelBuffers64[channel]);
            else
                // Narrow only at the native float32 boundary; transport remains float64.
                std::copy_n(input[channel].data() + offset, count, main_input.channelBuffers32[channel]);
        }

        // Apply each pending edit once, at the beginning of this block.
        s.input_changes.clearQueue();
        for (const auto& edit : s.handler->pending) {
            int32 queue_index = 0, point_index = 0;
            auto* queue = s.input_changes.addParameterData(edit.first, queue_index);
            if (!queue) throw std::runtime_error("Could not allocate parameter queue");
            check(queue->addPoint(0, edit.second, point_index), "parameter point");
        }
        s.output_changes.clearQueue();
        s.output_events.clear();
        if (processing_ms) {
            // Measure only DSP callbacks; buffer routing and parameter/controller work stay outside the interval.
            const auto start = std::chrono::steady_clock::now();
            const auto result = s.processor->process(s.data);
            const auto finish = std::chrono::steady_clock::now();
            *processing_ms += std::chrono::duration<double, std::milli>(finish - start).count();
            check(result, "process");
        } else {
            // Normal live analysis does not pay for per-block timer calls.
            check(s.processor->process(s.data), "process");
        }
        s.handler->pending.clear();

        // Synchronize processor feedback and honor the selected main output's silence flags.
        if (s.controller) {
            for (int32 i = 0; i < s.output_changes.getParameterCount(); ++i) {
                auto* queue = s.output_changes.getParameterData(i);
                int32 position = 0;
                ParamValue value = 0;
                if (queue && queue->getPoint(queue->getPointCount() - 1, position, value) == kResultOk)
                    s.controller->setParamNormalized(queue->getParameterId(), value);
            }
        }
        for (int32 channel = 0; channel < s.channel_count; ++channel) {
            if (!(s.data.outputs[s.main_output].silenceFlags & (uint64(1) << channel))) {
                if (s.sample_size == kSample64)
                    std::copy_n(s.data.outputs[s.main_output].channelBuffers64[channel], count, output[channel].data() + offset);
                else
                    std::copy_n(s.data.outputs[s.main_output].channelBuffers32[channel], count, output[channel].data() + offset);
            }
        }
    }
    return output;
}

/** Benchmark the existing processor with one second of native, unpaced input.
 * @param stimulus "noise" for independent uniform channel samples, or "impulse" for a unit Dirac followed by zeros.
 */
PerformanceResult PluginHost::performance(const std::string& stimulus) {
    auto& s = *impl_;
    // Use actual native settings; never recreate, reset or switch the plugin to an offline configuration.
    if (!s.processing) throw std::runtime_error("Configure processing first");
    if (stimulus != "noise" && stimulus != "impulse")
        throw std::invalid_argument("Performance stimulus must be noise or impulse");
    const auto samples = static_cast<std::uint32_t>(std::llround(s.sample_rate));
    const auto blocks = (samples + s.block_size - 1) / s.block_size;
    const auto processing_samples = blocks * s.block_size;
    // Benchmark full processing blocks, while reporting the nominal one-second sample count.
    Audio input(s.channel_count, std::vector<double>(processing_samples, 0.0));
    if (stimulus == "noise") {
        // Signal generation is outside the timed DSP calls; sequential draws give uncorrelated stereo inputs.
        std::mt19937_64 random(std::random_device{}());
        std::uniform_real_distribution<double> distribution(-1.0, 1.0);
        for (auto& channel : input)
            for (auto& sample : channel) sample = distribution(random);
    } else {
        // Each channel receives one unit impulse followed by zeros, including the rounded-up tail.
        for (auto& channel : input) channel.front() = 1.0;
    }
    double milliseconds = 0.0;
    process(input, &milliseconds);
    return {samples, static_cast<std::uint32_t>(blocks), milliseconds};
}

PrecisionSupport PluginHost::precision_support() const {
    // These flags describe I/O support, not the plugin's internal arithmetic precision.
    if (!impl_->processor) throw std::runtime_error("Load a plugin first");
    return impl_->precision;
}

std::string PluginHost::processing_precision() const {
    // No precision is reported until setupProcessing and activation succeeded.
    if (!impl_->processing) throw std::runtime_error("Configure processing first");
    return impl_->sample_size == kSample64 ? "float64" : "float32";
}

/** Save processor and optional controller state using the SDK preset format.
 * @param path Destination .vstpreset file; its existing contents may be replaced.
 */
void PluginHost::save_preset(const std::string& path) {
    // Requests are serialized with audio, so state is read without concurrent process calls.
    auto& s = *impl_;
    if (!s.component) throw std::runtime_error("Load a plugin first");
    MemoryStream component_state, controller_state;
    check(s.component->getState(&component_state), "get component state");
    check(component_state.seek(0, IBStream::kIBSeekSet, nullptr), "rewind component state");
    IBStream* controller_stream = nullptr;
    if (s.controller) {
        const auto result = s.controller->getState(&controller_state);
        if (result == kResultOk) {
            check(controller_state.seek(0, IBStream::kIBSeekSet, nullptr), "rewind controller state");
            controller_stream = &controller_state;
        } else if (result != kNotImplemented) {
            check(result, "get controller state");
        }
    }

    // The SDK writes a class-ID-tagged component chunk and any controller-only state.
    auto file = owned(FileStream::open(path.c_str(), "wb"));
    if (!file) throw std::runtime_error("Cannot create VST3 preset file");
    const auto uid = FUID::fromTUID(s.provider->getClassInfo().ID().data());
    if (!PresetFile::savePreset(file, uid, &component_state, controller_stream))
        throw std::runtime_error("Could not save VST3 preset");
}

/** Restore preset state into the currently loaded inactive instance.
 * @param path Existing .vstpreset file for this effect class, restored before activation.
 */
void PluginHost::load_preset(const std::string& path) {
    // Restoring before activation avoids changing a running plugin's processing lifecycle.
    auto& s = *impl_;
    if (!s.component) throw std::runtime_error("Load a plugin first");
    if (s.active) throw std::runtime_error("Restore preset before configuring processing");
    auto file = owned(FileStream::open(path.c_str(), "rb"));
    if (!file) throw std::runtime_error("Cannot open VST3 preset file");
    const auto uid = FUID::fromTUID(s.provider->getClassInfo().ID().data());
    if (!PresetFile::loadPreset(file, uid, s.component, s.controller))
        throw std::runtime_error("Could not restore VST3 preset or plugin class does not match");
    s.handler->pending.clear();
    s.handler->restart_flags = 0;
}

std::uint32_t PluginHost::latency() const {
    // Query each time so parameter-dependent latency is not cached incorrectly.
    if (!impl_->processor) throw std::runtime_error("Load a plugin first");
    return impl_->processor->getLatencySamples();
}

std::int32_t PluginHost::channels() const {
    // Configuration establishes the bus layout used by process().
    if (!impl_->processing) throw std::runtime_error("Configure processing first");
    return impl_->channel_count;
}
} // namespace analyzer
