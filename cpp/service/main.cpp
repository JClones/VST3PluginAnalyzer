// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Isolated VST3 host service dispatching framed IPC commands and audio while servicing native editor events.
 */
#include "host/plugin_host.h"
#include "host/native_editor.h"
#include "ipc/unix_socket.h"
#include "ui_launcher.h"
#include <rapidjson/document.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>
#include <cmath>
#include <csignal>
#include <iostream>
#include <memory>
#include <limits>
#include <set>
#include <stdexcept>

namespace {
namespace wire = analyzer::protocol;
using Json = rapidjson::Value;
using Allocator = rapidjson::Document::AllocatorType;

/** Copy a UTF-8 string into the response document's lifetime.
 * @param value UTF-8 text copied into response-owned storage.
 * @param allocator Response document allocator owning the copied string.
 */
Json text(const std::string& value, Allocator& allocator) {
    // Never retain pointers into temporary SDK strings.
    return Json(value.data(), static_cast<rapidjson::SizeType>(value.size()), allocator);
}
/** Read a required field without RapidJSON's assertion-based unchecked lookup.
 * @param object Validated JSON request object to inspect.
 * @param name Required property name; missing fields raise an ordinary request error.
 */
const Json& field(const Json& object, const char* name) {
    // Missing or incorrectly typed fields are ordinary request errors.
    const auto found = object.FindMember(name);
    if (found == object.MemberEnd()) throw std::invalid_argument(std::string("Missing field: ") + name);
    return found->value;
}
/** Read a required string and disallow embedded nulls in paths and command names.
 * @param object JSON object containing the requested field.
 * @param name Required string property name; embedded null bytes are rejected.
 */
std::string string_field(const Json& object, const char* name) {
    // A length-aware copy prevents silent truncation when calling native APIs.
    const auto& value = field(object, name);
    if (!value.IsString()) throw std::invalid_argument(std::string("Expected string: ") + name);
    std::string result(value.GetString(), value.GetStringLength());
    if (result.find('\0') != std::string::npos) throw std::invalid_argument("Embedded null in string");
    return result;
}
/** Read a finite JSON number.
 * @param object JSON object containing the requested field.
 * @param name Required finite numeric property name; strings and booleans are rejected.
 */
double number_field(const Json& object, const char* name) {
    // Strict numeric typing keeps booleans and numeric strings out of settings.
    const auto& value = field(object, name);
    if (!value.IsNumber() || !std::isfinite(value.GetDouble()))
        throw std::invalid_argument(std::string("Expected finite number: ") + name);
    return value.GetDouble();
}
/** Read an unsigned 32-bit integer with no float-to-integer coercion.
 * @param object JSON object containing the requested field.
 * @param name Required unsigned 32-bit integer property name.
 */
std::uint32_t integer_field(const Json& object, const char* name) {
    // IDs and sizes have explicit ranges shared with the Python client.
    const auto& value = field(object, name);
    if (!value.IsUint()) throw std::invalid_argument(std::string("Expected uint32: ") + name);
    return value.GetUint();
}
/** Serialize native editor state with nullable geometry when no window has been created.
 * @param state Native appearance to serialize, including whether geometry exists.
 * @param result Output JSON object receiving visible and geometry fields.
 * @param allocator Response document allocator for the geometry object.
 */
void editor_json(const analyzer::EditorState& state, Json& result, Allocator& allocator) {
    // Native coordinates remain opaque to Python, which only persists this metadata.
    result.AddMember("visible", state.visible, allocator);
    Json geometry(rapidjson::kNullType);
    if (state.has_geometry) {
        geometry.SetObject();
        geometry.AddMember("x", state.x, allocator);
        geometry.AddMember("y", state.y, allocator);
        geometry.AddMember("width", state.width, allocator);
        geometry.AddMember("height", state.height, allocator);
    }
    result.AddMember("geometry", geometry, allocator);
}

/** Validate an editor appearance request before creating or resizing a native window.
 * @param request JSON request containing explicit visibility and optional validated geometry.
 */
analyzer::EditorState editor_state_field(const Json& request) {
    // Visibility is always explicit; null or absent geometry means the plugin's default size.
    analyzer::EditorState state;
    const auto& visible = field(request, "visible");
    if (!visible.IsBool()) throw std::invalid_argument("Expected boolean: visible");
    state.visible = visible.GetBool();
    if (request.HasMember("geometry") && !request["geometry"].IsNull()) {
        const auto& geometry = request["geometry"];
        if (!geometry.IsObject()) throw std::invalid_argument("Expected editor geometry object");
        state.x = number_field(geometry, "x");
        state.y = number_field(geometry, "y");
        state.width = number_field(geometry, "width");
        state.height = number_field(geometry, "height");
        if (std::abs(state.x) > 1000000 || std::abs(state.y) > 1000000 ||
            state.width < 1 || state.height < 1 || state.width > 16384 || state.height > 16384)
            throw std::invalid_argument("Invalid editor geometry");
        state.has_geometry = true;
    }
    return state;
}

/** Serialize response metadata while retaining the separately framed binary payload.
 * @param response Complete JSON response object to serialize as UTF-8 metadata.
 * @param payload Optional binary response bytes moved into the returned frame.
 */
wire::Frame encode(const Json& response, std::vector<std::uint8_t> payload = {}) {
    // RapidJSON owns correct escaping; the transport only sees UTF-8 bytes.
    rapidjson::StringBuffer buffer;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
    if (!response.Accept(writer)) throw std::runtime_error("Response contains invalid JSON values");
    return {std::string(buffer.GetString(), buffer.GetSize()), std::move(payload)};
}
/** Convert advertised effect classes into ordinary JSON metadata.
 * @param classes Discovered VST3 audio-effect class descriptors.
 * @param allocator Response document allocator owning the class array and its strings.
 */
Json classes_json(const std::vector<analyzer::PluginClass>& classes, Allocator& allocator) {
    // Include stable IDs so multi-class modules can be selected explicitly.
    Json result(rapidjson::kArrayType);
    for (const auto& item : classes) {
        Json object(rapidjson::kObjectType);
        object.AddMember("id", text(item.id, allocator), allocator);
        object.AddMember("name", text(item.name, allocator), allocator);
        object.AddMember("vendor", text(item.vendor, allocator), allocator);
        object.AddMember("version", text(item.version, allocator), allocator);
        result.PushBack(object, allocator);
    }
    return result;
}

/** Serialized command session; the socket service owns exactly one plugin instance. */
class Session {
public:
    /** Dispatch one validated frame and convert ordinary failures into structured errors.
     * @param frame Received request metadata and optional audio payload to validate and dispatch.
     */
    wire::Frame handle(const wire::Frame& frame) {
        // Validate JSON, envelope fields and duplicate keys before touching the plugin.
        rapidjson::Document request, response;
        response.SetObject();
        auto& allocator = response.GetAllocator();
        response.AddMember("id", Json(), allocator);
        try {
            if (frame.json.find('\0') != std::string::npos)
                throw std::invalid_argument("Raw null byte in JSON metadata");
            request.Parse<rapidjson::kParseValidateEncodingFlag>(frame.json.data(), frame.json.size());
            if (request.HasParseError() || !request.IsObject())
                throw std::invalid_argument("Expected a UTF-8 JSON request object");
            std::set<std::string> names;
            for (auto it = request.MemberBegin(); it != request.MemberEnd(); ++it)
                if (!names.emplace(it->name.GetString(), it->name.GetStringLength()).second)
                    throw std::invalid_argument("Duplicate request field");
            response["id"].SetUint(integer_field(request, "id"));
            const auto command = string_field(request, "cmd");
            if (command != "process" && !frame.payload.empty())
                throw std::invalid_argument("Only process accepts a binary payload");
            if (!ready_ && command != "hello")
                throw std::invalid_argument("hello handshake required first");

            // Each command returns a result object and optionally audio bytes.
            Json result(rapidjson::kObjectType);
            std::vector<std::uint8_t> audio;
            dispatch(command, request, frame.payload, result, allocator, audio);
            response.AddMember("ok", true, allocator);
            response.AddMember("result", result, allocator);
            return encode(response, std::move(audio));
        } catch (const std::exception& error) {
            // Frame errors are handled by transport; a valid frame can recover from command errors.
            response.RemoveMember("ok");
            response.RemoveMember("result");
            Json detail(rapidjson::kObjectType);
            const bool invalid = dynamic_cast<const std::invalid_argument*>(&error) != nullptr;
            detail.AddMember("code", text(invalid ? "invalid_request" : "host_error", allocator), allocator);
            detail.AddMember("message", text(error.what(), allocator), allocator);
            response.AddMember("ok", false, allocator);
            response.AddMember("error", detail, allocator);
            return encode(response);
        }
    }
    /** Report whether shutdown was explicitly requested after its response is sent. */
    bool stopping() const {
        // The caller sends the final response before closing the stream.
        return stopping_;
    }
private:
    /** Return current plugin metadata without requiring processing configuration.
     * @param result Output JSON object receiving loaded-plugin, bus and precision metadata.
     * @param allocator Response document allocator owning the metadata arrays and strings.
     */
    void info(Json& result, Allocator& allocator) {
        // Host queries reject unloaded sessions instead of returning stale metadata.
        const auto buses = host_.buses();
        result.AddMember("path", text(path_, allocator), allocator);
        result.AddMember("class_id", text(class_id_, allocator), allocator);
        result.AddMember("latency_samples", host_.latency(), allocator);
        result.AddMember("configured", configured_, allocator);
        const auto support = host_.precision_support();
        Json supported(rapidjson::kArrayType);
        if (support.float32) supported.PushBack(Json("float32"), allocator);
        if (support.float64) supported.PushBack(Json("float64"), allocator);
        result.AddMember("supported_precisions", supported, allocator);
        if (configured_)
            result.AddMember("processing_precision", text(host_.processing_precision(), allocator), allocator);
        Json list(rapidjson::kArrayType);
        for (const auto& bus : buses) {
            Json item(rapidjson::kObjectType);
            item.AddMember("name", text(bus.name, allocator), allocator);
            item.AddMember("input", bus.input, allocator);
            item.AddMember("audio", bus.audio, allocator);
            item.AddMember("channels", bus.channels, allocator);
            item.AddMember("main", bus.main, allocator);
            item.AddMember("flags", bus.flags, allocator);
            list.PushBack(item, allocator);
        }
        result.AddMember("buses", list, allocator);
    }
    /** Execute a supported command; measurement algorithms remain outside this process.
     * @param command Validated command name selecting the requested host operation.
     * @param request Parsed request object supplying command-specific arguments.
     * @param payload Received binary audio bytes; accepted only by the process command.
     * @param result Output JSON object populated with command-specific response metadata.
     * @param allocator Response document allocator used by result and its child values.
     * @param audio Output binary payload filled with processed float64 audio, or left empty.
     */
    void dispatch(const std::string& command, const Json& request,
                  const std::vector<std::uint8_t>& payload, Json& result,
                  Allocator& allocator, std::vector<std::uint8_t>& audio) {
        // Handshake advertises the limits and currently implemented capabilities.
        if (command == "hello") {
            if (integer_field(request, "protocol_version") != wire::version)
                throw std::invalid_argument("Unsupported protocol version");
            ready_ = true;
            result.AddMember("protocol_version", wire::version, allocator);
            result.AddMember("max_json_bytes", wire::max_json, allocator);
            result.AddMember("max_audio_bytes", wire::max_audio, allocator);
            result.AddMember("native_editor", analyzer::native_editor_supported(), allocator);
            result.AddMember("sample_format", "f64le", allocator);
            result.AddMember("layout", "planar", allocator);
        } else if (command == "list_classes") {
            result.AddMember("classes", classes_json(analyzer::PluginHost::inspect(
                string_field(request, "path")), allocator), allocator);
        } else if (command == "load_plugin") {
            // Loading replaces an old instance, even when the new plugin fails to load.
            const auto path = string_field(request, "path");
            const auto selected = request.HasMember("class_id") ? string_field(request, "class_id") : "";
            host_.unload();
            configured_ = false;
            path_.clear();
            class_id_.clear();
            const auto classes = analyzer::PluginHost::inspect(path);
            host_.load(path, selected);
            path_ = path;
            class_id_ = selected.empty() ? classes.front().id : selected;
            info(result, allocator);
            result.AddMember("classes", classes_json(classes, allocator), allocator);
        } else if (command == "unload_plugin") {
            host_.unload();
            configured_ = false;
            path_.clear();
            class_id_.clear();
        } else if (command == "plugin_info") {
            info(result, allocator);
        } else if (command == "configure") {
            const auto rate = number_field(request, "sample_rate");
            const auto size = integer_field(request, "block_size");
            if (size > 65536) throw std::invalid_argument("Block size exceeds host limit");
            configured_ = false;
            const auto precision = request.HasMember("precision") ? string_field(request, "precision") : "auto";
            const auto mode = request.HasMember("processing_mode") ? string_field(request, "processing_mode") : "offline";
            host_.configure(rate, static_cast<std::int32_t>(size), precision, mode);
            configured_ = true;
            result.AddMember("sample_rate", rate, allocator);
            result.AddMember("processing_mode", text(mode, allocator), allocator);
            result.AddMember("block_size", size, allocator);
            result.AddMember("channels", host_.channels(), allocator);
            result.AddMember("processing_precision", text(host_.processing_precision(), allocator), allocator);
            result.AddMember("sample_format", "f64le", allocator);
            result.AddMember("latency_samples", host_.latency(), allocator);
        } else if (command == "get_latency") {
            result.AddMember("latency_samples", host_.latency(), allocator);
        } else if (command == "save_preset") {
            host_.save_preset(string_field(request, "path"));
        } else if (command == "load_preset") {
            host_.load_preset(string_field(request, "path"));
        } else if (command == "get_editor") {
            editor_json(host_.editor_state(), result, allocator);
        } else if (command == "raise_editor") {
            // Ordering an existing window leaves visibility, geometry and audio untouched.
            host_.raise_editor();
            editor_json(host_.editor_state(), result, allocator);
        } else if (command == "show_editor") {
            const auto& visible = field(request, "visible");
            if (!visible.IsBool()) throw std::invalid_argument("Expected boolean: visible");
            host_.show_editor(visible.GetBool());
            editor_json(host_.editor_state(), result, allocator);
        } else if (command == "restore_editor") {
            host_.restore_editor(editor_state_field(request));
            editor_json(host_.editor_state(), result, allocator);
        } else if (command == "has_editor") {
            result.AddMember("available", host_.has_editor(), allocator);
        } else if (command == "get_programs") {
            // Keep factory program identities separate from human-readable menu names.
            Json list(rapidjson::kArrayType);
            for (const auto& program : host_.programs()) {
                Json item(rapidjson::kObjectType);
                item.AddMember("parameter_id", program.parameter_id, allocator);
                item.AddMember("index", program.index, allocator);
                item.AddMember("name", text(program.name, allocator), allocator);
                item.AddMember("selected", program.selected, allocator);
                list.PushBack(item, allocator);
            }
            result.AddMember("programs", list, allocator);
        } else if (command == "select_program") {
            const auto index = integer_field(request, "index");
            if (index > std::numeric_limits<std::int32_t>::max())
                throw std::invalid_argument("Invalid program index");
            host_.select_program(integer_field(request, "parameter_id"), static_cast<std::int32_t>(index));
        } else if (command == "get_parameters") {
            Json list(rapidjson::kArrayType);
            for (const auto& parameter : host_.parameters()) {
                Json item(rapidjson::kObjectType);
                item.AddMember("id", parameter.id, allocator);
                item.AddMember("title", text(parameter.title, allocator), allocator);
                item.AddMember("units", text(parameter.units, allocator), allocator);
                item.AddMember("display", text(parameter.display, allocator), allocator);
                item.AddMember("value", parameter.value, allocator);
                item.AddMember("default_value", parameter.default_value, allocator);
                item.AddMember("steps", parameter.steps, allocator);
                item.AddMember("flags", parameter.flags, allocator);
                list.PushBack(item, allocator);
            }
            result.AddMember("parameters", list, allocator);
        } else if (command == "set_parameter") {
            host_.set_parameter(integer_field(request, "parameter_id"), number_field(request, "value"));
        } else if (command == "set_parameter_text") {
            // Let the plugin parse its logical units, then queue the same edit as a normalized control.
            result.AddMember("value", host_.set_parameter_text(integer_field(request, "parameter_id"),
                             string_field(request, "text")), allocator);
        } else if (command == "performance") {
            // Return only timing metadata; no audio generation or transport is inside the timed native callbacks.
            if (!configured_) throw std::invalid_argument("Configure processing first");
            const auto measurement = host_.performance(string_field(request, "stimulus"));
            result.AddMember("samples", measurement.samples, allocator);
            result.AddMember("blocks", measurement.blocks, allocator);
            result.AddMember("processing_ms", measurement.processing_ms, allocator);
            result.AddMember("processing_precision", text(host_.processing_precision(), allocator), allocator);
        } else if (command == "process") {
            // Validate dimensions with wide arithmetic before allocating channel vectors.
            if (!configured_) throw std::invalid_argument("Configure processing first");
            const auto channels = integer_field(request, "channels");
            const auto frames = integer_field(request, "frames");
            if (channels != static_cast<std::uint32_t>(host_.channels()) ||
                std::uint64_t(channels) * frames * 8 != payload.size() ||
                string_field(request, "sample_format") != "f64le" ||
                string_field(request, "layout") != "planar")
                throw std::invalid_argument("Audio dimensions, layout or format do not match payload");
            analyzer::Audio input(channels, std::vector<double>(frames));
            const bool native32 = host_.processing_precision() == "float32";
            std::size_t index = 0;
            for (auto& channel : input) {
                for (auto& sample : channel) {
                    sample = wire::decode_float(payload.data() + index);
                    if (!std::isfinite(sample) || (native32 &&
                        std::abs(sample) > std::numeric_limits<float>::max())) throw std::invalid_argument("Input audio must be finite and representable at native precision");
                    index += 8;
                }
            }

            // The host splits the chunk into configured blocks and retains state between requests.
            const auto output = host_.process(input);
            audio.reserve(payload.size());
            for (const auto& channel : output)
                for (const auto sample : channel) wire::append_float(audio, sample);
            result.AddMember("channels", channels, allocator);
            result.AddMember("frames", frames, allocator);
            result.AddMember("processing_precision", text(host_.processing_precision(), allocator), allocator);
            result.AddMember("sample_format", "f64le", allocator);
            result.AddMember("layout", "planar", allocator);
            result.AddMember("latency_samples", host_.latency(), allocator);
        } else if (command == "shutdown" || command == "exit") {
            // Terminate the plugin before acknowledging a clean service shutdown.
            host_.unload();
            stopping_ = true;
        } else {
            throw std::invalid_argument("Unknown or unsupported command: " + command);
        }
    }
    analyzer::PluginHost host_; ///< VST3 lifecycle and processing owner.
    std::string path_; ///< Current module path, empty after unloading.
    std::string class_id_; ///< Selected stable class identifier.
    bool ready_ = false; ///< Handshake has succeeded for this connection.
    bool configured_ = false; ///< A valid processing setup is available.
    bool stopping_ = false; ///< Shutdown acknowledged; exit after sending its response.
};
} // namespace

/** Serve one private Unix socket session and isolate plugins in this executable.
 * @param argc Number of command-line arguments, including the executable name.
 * @param argv Null-terminated argument vector; usage text describes accepted arguments.
 */
int main(int argc, char** argv) {
    try {
        std::signal(SIGPIPE, SIG_IGN);
        std::unique_ptr<analyzer::UILauncher> ui;
        std::unique_ptr<analyzer::ipc::UnixSocket> socket;
        const std::string option = argc > 1 ? argv[1] : "";
        if (option == "--socket" || option == "--socket-fd") {
            if (argc != 3) throw std::invalid_argument("Service options require exactly one argument");
            if (option == "--socket") {
                socket = std::make_unique<analyzer::ipc::UnixSocket>(argv[2]);
            } else {
                std::size_t end = 0;
                const auto descriptor = std::stoi(argv[2], &end);
                if (end != std::string(argv[2]).size()) throw std::invalid_argument("Invalid descriptor");
                socket = std::make_unique<analyzer::ipc::UnixSocket>(descriptor);
            }
        } else {
            ui = std::make_unique<analyzer::UILauncher>();
            socket = std::make_unique<analyzer::ipc::UnixSocket>(ui->socket_path());
            ui->start(argc, argv);
            while (!socket->client_pending(50)) {
                if (!ui->running()) return ui->wait();
            }
        }
        socket->accept_client();
        wire::Frame final_response;
        bool clean_shutdown = false;
        {
            Session session;
            wire::Frame frame;

            // UI callbacks, control commands and audio remain serialized on the native main thread.
            while (true) {
                analyzer::pump_editor_events();
                if (!socket->readable(5)) continue;
                if (!socket->receive(frame)) break;
                auto response = session.handle(frame);
                if (session.stopping()) {
                    final_response = std::move(response);
                    clean_shutdown = true;
                    break;
                }
                socket->send(response);
            }
        }
        // Acknowledge only after all plugin/session destruction succeeds.
        if (clean_shutdown) socket->send(final_response);
        socket.reset();
        return ui ? ui->wait() : 0;
    } catch (const std::exception& error) {
        // Unrecoverable transport failures end the entire application instance.
        std::cerr << "Service error: " << error.what() << '\n';
        return 1;
    }
}
