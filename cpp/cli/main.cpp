// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Command-line VST3 host probe for inspecting plugins, setting parameters and checking audio processing.
 */
#include "host/plugin_host.h"
#include <cmath>
#include <filesystem>
#include <iostream>
#include <stdexcept>

namespace {
/** Compute a compact diagnostic peak from returned audio; no analyzer DSP lives here.
 * @param audio Channel-major returned audio whose largest absolute sample is reported.
 */
double peak(const analyzer::Audio& audio) {
    // Scan all channels so disconnected or silent outputs are visible in the probe.
    double result = 0;
    for (const auto& channel : audio)
        for (double sample : channel) result = std::max(result, std::abs(double(sample)));
    return result;
}

/** Parse a numeric option and reject trailing characters.
 * @param text Complete command-line token to parse as a finite floating-point number.
 */
double number(const std::string& text) {
    // std::stod otherwise accepts malformed arguments such as "48000Hz".
    std::size_t end = 0;
    const double result = std::stod(text, &end);
    if (end != text.size() || !std::isfinite(result))
        throw std::invalid_argument("Invalid number: " + text);
    return result;
}
} // namespace

/** Inspect and exercise one plugin; report ordinary failures with a nonzero exit code.
 * @param argc Number of command-line arguments, including the executable name.
 * @param argv Null-terminated argument vector; usage text describes accepted arguments.
 */
int main(int argc, char** argv) {
    try {
        // Collect explicit plugin/class/settings options without a CLI dependency.
        if (argc < 2 || std::string(argv[1]) == "--help") {
            std::cout << "Usage: vst3-host-probe PLUGIN.vst3 [--list] [--class ID]\n"
                         "       [--sample-rate HZ] [--block-size N] [--set ID VALUE]\n";
            return argc < 2 ? 2 : 0;
        }
        const auto path = std::filesystem::absolute(argv[1]).string();
        std::string class_id;
        double sample_rate = 48000;
        std::int32_t block_size = 512;
        bool list_only = false, has_edit = false;
        std::uint32_t parameter_id = 0;
        double parameter_value = 0;
        for (int i = 2; i < argc; ++i) {
            const std::string option = argv[i];
            if (option == "--list") { list_only = true; continue; }
            if (i + 1 >= argc) throw std::invalid_argument("Missing value for " + option);
            const std::string value = argv[++i];
            if (option == "--class") class_id = value;
            else if (option == "--sample-rate") sample_rate = number(value);
            else if (option == "--block-size") {
                const auto parsed = number(value);
                if (parsed < 1 || parsed > 65536 || parsed != std::floor(parsed))
                    throw std::invalid_argument("Block size must be an integer in [1, 65536]");
                block_size = static_cast<std::int32_t>(parsed);
            } else if (option == "--set") {
                const auto parsed = number(value);
                if (parsed < 0 || parsed > 4294967295.0 || parsed != std::floor(parsed))
                    throw std::invalid_argument("Parameter ID must be an unsigned 32-bit integer");
                parameter_id = static_cast<std::uint32_t>(parsed);
                if (i + 1 >= argc) throw std::invalid_argument("Missing parameter value");
                parameter_value = number(argv[++i]);
                has_edit = true;
            } else throw std::invalid_argument("Unknown option: " + option);
        }

        // Enumerate processor classes before creating an instance.
        for (const auto& info : analyzer::PluginHost::inspect(path))
            std::cout << "Class: " << info.name << " | " << info.id << " | "
                      << info.vendor << " | " << info.version << '\n';
        if (list_only) return 0;
        analyzer::PluginHost host;
        host.load(path, class_id);
        for (const auto& bus : host.buses())
            std::cout << "Bus: " << (bus.audio ? "audio " : "event ")
                      << (bus.input ? "input " : "output ") << bus.name << ", "
                      << bus.channels << " channels, " << (bus.main ? "main" : "auxiliary")
                      << ", flags " << bus.flags << '\n';
        host.configure(sample_rate, block_size);
        std::cout << "Audio: " << host.channels() << " channels, " << sample_rate
                  << " Hz, block " << block_size << ", latency " << host.latency() << " samples\n";
        for (const auto& parameter : host.parameters())
            std::cout << "Parameter " << parameter.id << ": " << parameter.title << " = "
                      << parameter.value << " (" << parameter.display << ' ' << parameter.units
                      << "), steps " << parameter.steps << ", flags " << parameter.flags << '\n';

        // Exercise consecutive processing calls with a bounded 1 kHz, -12 dBFS-peak stimulus.
        const auto samples = static_cast<std::size_t>(sample_rate / 10);
        analyzer::Audio input(host.channels(), std::vector<double>(samples));
        for (auto& channel : input)
            for (std::size_t i = 0; i < samples; ++i)
                channel[i] = static_cast<double>(0.25 * std::sin(2 * 3.141592653589793 * 1000 * i / sample_rate));
        std::cout << "Output peak before edit: " << peak(host.process(input)) << '\n';
        if (has_edit) {
            host.set_parameter(parameter_id, parameter_value);
            std::cout << "Output peak after edit: " << peak(host.process(input)) << '\n';
        }
        std::cout << "Reported latency: " << host.latency() << " samples\n";
        host.unload();
        return 0;
    } catch (const std::exception& error) {
        // Plugin crashes are outside C++ exception handling; the later service isolates them.
        std::cerr << "Host error: " << error.what() << '\n';
        return 1;
    }
}
