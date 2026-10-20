// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Validated binary framing and endian-safe float64 audio serialization, independent of sockets and the SDK.
 */
#include "protocol/frame.h"
#include <cstring>
#include <limits>
#include <stdexcept>

namespace analyzer::protocol {
namespace {
/** Write an unsigned 32-bit field in network byte order.
 * @param destination Pointer to at least four writable bytes for the network-order field.
 * @param value Unsigned 32-bit integer to encode in big-endian byte order.
 */
void put32(std::uint8_t* destination, std::uint32_t value) {
    // Write bytes explicitly rather than serializing native structs.
    for (int i = 0; i < 4; ++i) destination[i] = (value >> (24 - 8 * i)) & 0xff;
}
/** Read an unsigned 32-bit network-order field.
 * @param source Pointer to at least four readable bytes containing a network-order integer.
 */
std::uint32_t get32(const std::uint8_t* source) {
    // Promote before shifting to avoid signed-byte and integer-overflow issues.
    return (std::uint32_t(source[0]) << 24) | (std::uint32_t(source[1]) << 16) |
           (std::uint32_t(source[2]) << 8) | source[3];
}
} // namespace

/** Encode a fixed-size network-byte-order header after checking message limits.
 * @param frame Message whose metadata and payload lengths are validated and encoded.
 */
std::array<std::uint8_t, header_size> header(const Frame& frame) {
    // Validate before narrowing sizes into the fixed-width header fields.
    if (frame.json.empty() || frame.json.size() > max_json || frame.payload.size() > max_audio)
        throw std::invalid_argument("Frame size exceeds protocol limits");
    std::array<std::uint8_t, header_size> bytes {'V', '3', 'P', 'A', 0, version, 0, 0};
    put32(bytes.data() + 8, static_cast<std::uint32_t>(frame.json.size()));
    put32(bytes.data() + 12, static_cast<std::uint32_t>(frame.payload.size()));
    return bytes;
}

/** Validate a received wire header and return safe metadata/payload lengths.
 * @param bytes Exactly header_size wire bytes to validate before allocating message buffers.
 */
Lengths decode_header(const std::array<std::uint8_t, header_size>& bytes) {
    // Malformed framing cannot be recovered safely on a stream; callers close the connection.
    if (std::memcmp(bytes.data(), "V3PA", 4) || bytes[4] != 0 || bytes[5] != version ||
        bytes[6] != 0 || bytes[7] != 0)
        throw std::invalid_argument("Invalid frame magic, version or flags");
    Lengths lengths {get32(bytes.data() + 8), get32(bytes.data() + 12)};
    if (!lengths.json || lengths.json > max_json || lengths.payload > max_audio)
        throw std::invalid_argument("Frame size exceeds protocol limits");
    return lengths;
}

/** Decode one little-endian float64 without assuming native byte order.
 * @param bytes Pointer to at least eight readable little-endian IEEE-754 float64 bytes.
 */
double decode_float(const std::uint8_t* bytes) {
    // Reconstruct IEEE-754 bits; memcpy avoids aliasing and alignment assumptions.
    static_assert(sizeof(double) == 8 && std::numeric_limits<double>::is_iec559);
    std::uint64_t bits = 0;
    for (int i = 0; i < 8; ++i) bits |= std::uint64_t(bytes[i]) << (8 * i);
    double value;
    std::memcpy(&value, &bits, 8);
    return value;
}

/** Append one float64 value in little-endian wire byte order.
 * @param bytes Output vector extended by exactly eight wire-format bytes.
 * @param value Float64 sample whose IEEE-754 bit pattern is serialized.
 */
void append_float(std::vector<std::uint8_t>& bytes, double value) {
    // Preserve every output bit, including non-finite values emitted by a plugin.
    std::uint64_t bits;
    std::memcpy(&bits, &value, 8);
    for (int i = 0; i < 8; ++i) bytes.push_back((bits >> (8 * i)) & 0xff);
}
} // namespace analyzer::protocol
