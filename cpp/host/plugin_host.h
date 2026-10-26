// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Public host API and data types for VST3 loading, bus routing, processing, parameters, presets and editors.
 */
#pragma once

#include "host/native_editor.h"
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace analyzer {

/** One loadable audio-effect class exported by a VST3 module. */
struct PluginClass {
    std::string id; ///< Stable hexadecimal VST3 class identifier.
    std::string name; ///< Human-readable effect name.
    std::string vendor; ///< Manufacturer reported by the factory.
    std::string version; ///< Plugin version reported by the factory.
};

/** A controller parameter; values use the VST3 normalized range [0, 1]. */
struct Parameter {
    std::uint32_t id; ///< Stable parameter identifier, never a list index.
    std::string title; ///< UTF-8 parameter title.
    std::string units; ///< UTF-8 unit label.
    std::string display; ///< Plugin-formatted current value.
    double value; ///< Current normalized controller value.
    double default_value; ///< Declared normalized default.
    std::int32_t steps; ///< Zero for continuous parameters; otherwise discrete step count.
    std::int32_t flags; ///< Original VST3 ParameterInfo flags.
};

/** One factory program exposed by an SDK program-change parameter. */
struct Program {
    std::uint32_t parameter_id; ///< Program-change parameter identifying the owning unit.
    std::int32_t index; ///< Zero-based program index within that parameter's list.
    std::string name; ///< Plugin-provided UTF-8 program name.
    bool selected; ///< Whether the controller currently reports this program.
};

/** An advertised audio or event bus, before any future routing negotiation. */
struct Bus {
    std::string name; ///< Plugin-provided UTF-8 bus label.
    bool input; ///< True for input, false for output.
    bool audio; ///< True for audio, false for event/MIDI buses.
    std::int32_t channels; ///< Number of audio channels or event channels.
    bool main; ///< True for a main bus, false for an auxiliary bus.
    std::uint32_t flags; ///< Original SDK BusInfo flags, including default activation.
};

/** Channel-major float64 transport audio, with equally sized channels. */
using Audio = std::vector<std::vector<double>>;

/** Native DSP timing for one second rounded up to full blocks, excluding generation, routing and IPC. */
struct PerformanceResult {
    std::uint32_t samples; ///< Nominal one-second sample count for display, excluding the rounded-up processing tail.
    std::uint32_t blocks; ///< Number of full-sized native processing callbacks.
    double processing_ms; ///< Sum of monotonic-clock durations around IAudioProcessor::process(), in milliseconds.
};

/** Supported native plugin processing precisions, queried before configuration. */
struct PrecisionSupport {
    bool float32; ///< Processor accepts VST3 kSample32 buffers.
    bool float64; ///< Processor accepts VST3 kSample64 buffers.
};

/** Owns one effect instance. All calls are serialized on the owning thread.
 * Exceptions describe ordinary host failures. A plugin crash requires process isolation.
 * The first main input/output pair must be matching mono/stereo; additional inputs receive silence.
 */
class PluginHost {
public:
    /** Construct an empty host with a VST3 application context. */
    PluginHost();
    /** Stop processing and release every plugin reference before unloading its module. */
    ~PluginHost();
    /** Disallow copying this owner.
     * @param other Source instance; copying is forbidden because ownership is unique.
     */
    PluginHost(const PluginHost& other) = delete; ///< A plugin instance has unique ownership.
    /** Disallow copy assignment.
     * @param other Source instance; assignment cannot copy its uniquely owned resources.
     */
    PluginHost& operator=(const PluginHost& other) = delete; ///< Ownership cannot be copied.

    /** Inspect audio-effect classes without creating a processor instance.
     * @param path Filesystem path to the VST3 module bundle to inspect.
     */
    static std::vector<PluginClass> inspect(const std::string& path);
    /** Load a selected class ID; an omitted ID requires exactly one effect class.
     * @param path Filesystem path to the VST3 module bundle to load.
     * @param class_id Hexadecimal audio-effect class ID; empty selects the sole effect class.
     */
    void load(const std::string& path, const std::string& class_id = "");
    /** Unload the current instance; safe when empty and after partial initialization. */
    void unload() noexcept;
    /** Configure offline/realtime processing; auto prefers float64, explicit modes require support.
     * @param sample_rate Processing sample rate in Hz, from 8000 through 768000.
     * @param block_size Maximum samples per native processing block, from 1 through 65536.
     * @param precision Native buffer preference: auto, float32 or float64.
     * @param processing_mode SDK processing mode: offline or realtime; pacing remains the caller's responsibility.
     */
    void configure(double sample_rate, std::int32_t block_size, const std::string& precision = "auto",
                   const std::string& processing_mode = "offline");
    /** Return native float32/float64 support cached during inactive instance initialization. */
    PrecisionSupport precision_support() const;
    /** Return the configured native processing precision (float32 or float64). */
    std::string processing_precision() const;
    /** Save processor and optional controller state in the SDK's native VST3 preset format.
     * @param path Destination .vstpreset file; its existing contents may be replaced.
     */
    void save_preset(const std::string& path);
    /** Restore a matching preset into a freshly loaded, inactive instance before configuration.
     * @param path Existing .vstpreset file for this effect class, restored before activation.
     */
    void load_preset(const std::string& path);
    /** Query and retain the single optional editor view; later checks reuse it without creating a second view. */
    bool has_editor() const;
    /** Show or hide the plugin editor without changing the processing stream.
     * @param visible True shows or raises the native editor; false hides an existing window.
     */
    void show_editor(bool visible);
    /** Raise an existing visible editor without showing a closed editor or changing focus. */
    void raise_editor();
    /** Report native editor window visibility, including its close button. */
    bool editor_visible() const;
    /** Return current native editor appearance, including hidden window geometry. */
    EditorState editor_state() const;
    /** Restore editor visibility and geometry without reconfiguring audio processing.
     * @param state Saved visibility and optional native position/content size to restore.
     */
    void restore_editor(const EditorState& state);
    /** Enumerate factory programs associated with exposed program-change parameters. */
    std::vector<Program> programs() const;
    /** Select a factory program through its normalized SDK parameter.
     * @param parameter_id Stable SDK program-change parameter ID identifying the program list.
     * @param index Zero-based program index within that parameter's discrete range.
     */
    void select_program(std::uint32_t parameter_id, std::int32_t index);
    /** Enumerate all advertised audio/event input/output buses. */
    std::vector<Bus> buses() const;
    /** Return controller parameter descriptors and their current values. */
    std::vector<Parameter> parameters() const;
    /** Update the controller and queue a processor change at the next block's sample zero.
     * @param id Stable SDK ID of a writable controller parameter.
     * @param value Finite normalized parameter value in the inclusive range [0, 1].
     */
    void set_parameter(std::uint32_t id, double value);
    /** Parse a plugin-formatted value and queue the corresponding normalized edit.
     * @param id Stable SDK ID of a writable controller parameter.
     * @param text UTF-8 text interpreted by the plugin's getParamValueByString implementation.
     * @return Controller's resulting normalized value after applying the edit.
     */
    double set_parameter_text(std::uint32_t id, const std::string& text);
    /** Process consecutive blocks, retaining state between calls and allowing a short last block.
     * @param input Channel-major float64 samples for the main input; channels must have equal lengths.
     * @param processing_ms Optional output receiving summed native callback time; nullptr disables timing overhead.
     */
    Audio process(const Audio& input, double* processing_ms = nullptr);

    /** Time one second rounded up to full processing blocks without reconfiguration.
     * @param stimulus Either "noise" for independent uniform stereo noise or "impulse" for one unit impulse then zeros.
     */
    PerformanceResult performance(const std::string& stimulus);
    /** Return the processor's currently reported latency in samples. */
    std::uint32_t latency() const;
    /** Return the selected audio bus channel count after configuration. */
    std::int32_t channels() const;
private:
    struct Impl; ///< SDK types and lifecycle bookkeeping stay private to C++ hosting.
    std::unique_ptr<Impl> impl_; ///< Sole owner of module, processor and controller state.
};
} // namespace analyzer
