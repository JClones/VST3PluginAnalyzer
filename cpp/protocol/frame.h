// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Wire-protocol constants, message containers and binary header/audio serialization declarations.
 */
#pragma once
#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace analyzer::protocol {
constexpr std::uint16_t version = 2; ///< Current wire-protocol version.
constexpr std::uint32_t max_json = 1024 * 1024; ///< Maximum UTF-8 JSON bytes per frame.
constexpr std::uint32_t max_audio = 16 * 1024 * 1024; ///< Maximum binary payload bytes.
constexpr std::size_t header_size = 16; ///< Fixed header size, independent of C++ padding.

/** One complete application message; transport does not interpret its JSON. */
struct Frame {
    std::string json; ///< UTF-8 request or response metadata.
    std::vector<std::uint8_t> payload; ///< Optional little-endian planar float64 audio.
};

/** Lengths decoded from a validated header before allocating receive buffers. */
struct Lengths {
    std::uint32_t json; ///< Number of metadata bytes following the header.
    std::uint32_t payload; ///< Number of binary bytes following metadata.
};

/** Encode a fixed-size, network-byte-order header after checking size limits.
 * @param frame Message whose metadata and payload lengths are validated and encoded.
 */
std::array<std::uint8_t, header_size> header(const Frame& frame);
/** Reject magic/version/flags/size violations and return safe allocation lengths.
 * @param bytes Exactly header_size wire bytes to validate before allocating message buffers.
 */
Lengths decode_header(const std::array<std::uint8_t, header_size>& bytes);
/** Decode one IEEE-754 little-endian float64 without assuming native byte order.
 * @param bytes Pointer to at least eight readable little-endian IEEE-754 float64 bytes.
 */
double decode_float(const std::uint8_t* bytes);
/** Append one IEEE-754 float64 in little-endian wire byte order.
 * @param bytes Output vector extended by exactly eight wire-format bytes.
 * @param value Float64 sample whose IEEE-754 bit pattern is serialized.
 */
void append_float(std::vector<std::uint8_t>& bytes, double value);
} // namespace analyzer::protocol
