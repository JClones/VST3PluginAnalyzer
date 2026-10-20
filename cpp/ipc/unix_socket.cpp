// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Unix socket setup, bounded readiness polling and reliable framed reads and writes.
 */
#include "ipc/unix_socket.h"
#include <cerrno>
#include <cstring>
#include <stdexcept>
#include <sys/socket.h>
#include <poll.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

namespace analyzer::ipc {
namespace {
/** Raise a diagnostic containing the failed syscall and errno description.
 * @param operation Failed syscall name combined with the current errno diagnostic.
 */
void fail(const char* operation) {
    // Capture the diagnostic before further operations can change errno.
    throw std::runtime_error(std::string(operation) + ": " + std::strerror(errno));
}
/** Read exactly a byte count; allow clean EOF only before a new frame header.
 * @param fd Connected readable socket descriptor; ownership remains with the caller.
 * @param destination Writable memory receiving exactly size bytes.
 * @param size Number of bytes to read into destination.
 * @param allow_eof Allow clean EOF only before any bytes have been read, as at a frame boundary.
 */
bool read_exact(int fd, void* destination, std::size_t size, bool allow_eof = false) {
    // Short reads and EINTR are normal stream behavior, not message boundaries.
    auto* bytes = static_cast<std::uint8_t*>(destination);
    std::size_t done = 0;
    while (done < size) {
        const auto count = ::recv(fd, bytes + done, size - done, 0);
        if (count == 0) {
            if (allow_eof && done == 0) return false;
            throw std::runtime_error("Truncated frame: client disconnected");
        }
        if (count < 0) {
            if (errno == EINTR) continue;
            fail("recv");
        }
        done += static_cast<std::size_t>(count);
    }
    return true;
}
/** Write exactly a byte count after SIGPIPE has been disabled by the service.
 * @param fd Connected writable socket descriptor; ownership remains with the caller.
 * @param source Readable memory containing the bytes to send.
 * @param size Number of bytes to send from source.
 */
void write_exact(int fd, const void* source, std::size_t size) {
    // Loop until the entire buffer is sent, preserving frame order.
    const auto* bytes = static_cast<const std::uint8_t*>(source);
    std::size_t done = 0;
    while (done < size) {
        const auto count = ::send(fd, bytes + done, size - done, 0);
        if (count < 0) {
            if (errno == EINTR) continue;
            fail("send");
        }
        if (!count) throw std::runtime_error("Socket write made no progress");
        done += static_cast<std::size_t>(count);
    }
}
} // namespace

/** Create a private named Unix-domain listener.
 * @param path New Unix-domain socket pathname to bind; an existing entry is never overwritten.
 */
UnixSocket::UnixSocket(const std::string& path) : path_(path) {
    // Enforce the stricter native path limit before creating any filesystem entry.
    sockaddr_un address {};
    if (path.empty() || path.find('\0') != std::string::npos || path.size() >= sizeof(address.sun_path))
        throw std::invalid_argument("Unix socket path is empty or too long");
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    listener_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (listener_ < 0) fail("socket");
    try {
        // bind refuses pre-existing files; never unlink a path that we did not create.
        if (::bind(listener_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) fail("bind");
        bound_ = true;
        if (::chmod(path.c_str(), 0600) < 0) fail("chmod socket");
        if (::listen(listener_, 1) < 0) fail("listen");
    } catch (...) {
        // Constructor failures require explicit cleanup because no destructor will run.
        ::close(listener_);
        if (bound_) ::unlink(path_.c_str());
        throw;
    }
}

UnixSocket::~UnixSocket() {
    // Closing the connection causes blocked client calls to observe disconnect.
    if (client_ >= 0) ::close(client_);
    if (listener_ >= 0) ::close(listener_);
    if (bound_) ::unlink(path_.c_str());
}

/** Adopt and validate an inherited connected Unix-domain descriptor.
 * @param connected_fd Connected AF_UNIX stream descriptor whose ownership is transferred to this object.
 */
UnixSocket::UnixSocket(int connected_fd) : client_(connected_fd) {
    // Validate inherited descriptors before treating them as private local IPC endpoints.
    sockaddr_storage address {};
    socklen_t address_size = sizeof(address);
    int type = 0;
    socklen_t type_size = sizeof(type);
    if (connected_fd < 3 || ::getsockname(connected_fd, reinterpret_cast<sockaddr*>(&address),
        &address_size) < 0 || address.ss_family != AF_UNIX ||
        ::getsockopt(connected_fd, SOL_SOCKET, SO_TYPE, &type, &type_size) < 0 || type != SOCK_STREAM ||
        ::getpeername(connected_fd, reinterpret_cast<sockaddr*>(&address), &address_size) < 0) {
        if (connected_fd >= 3) ::close(connected_fd);
        throw std::invalid_argument("Expected an inherited AF_UNIX stream descriptor");
    }
}

void UnixSocket::accept_client() {
    // One client owns the instance; disconnect terminates this service session.
    if (client_ < 0) {
        do { client_ = ::accept(listener_, nullptr, nullptr); } while (client_ < 0 && errno == EINTR);
    }
    if (client_ < 0) fail("accept");
    timeval timeout {30, 0};
    if (::setsockopt(client_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) < 0 ||
        ::setsockopt(client_, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) < 0)
        fail("setsockopt timeout");
}

/** Wait for incoming frame data or a peer disconnection.
 * @param timeout_ms Maximum poll wait in milliseconds; zero checks immediately and -1 waits indefinitely.
 */
bool UnixSocket::readable(int timeout_ms) {
    // A bounded idle wait lets the owning main thread dispatch native editor events.
    pollfd descriptor {client_, POLLIN, 0};
    int ready;
    do { ready = ::poll(&descriptor, 1, timeout_ms); } while (ready < 0 && errno == EINTR);
    if (ready < 0) fail("poll");
    return ready > 0;
}

/** Receive one complete frame, returning false only for EOF between frames.
 * @param frame Output message replaced with validated JSON metadata and optional binary payload.
 */
bool UnixSocket::receive(protocol::Frame& frame) {
    // Validate the fixed header before allocating metadata or audio buffers.
    // Idle clients may keep the analyzer open indefinitely; timeout begins with frame data.
    pollfd descriptor {client_, POLLIN, 0};
    int ready;
    do { ready = ::poll(&descriptor, 1, -1); } while (ready < 0 && errno == EINTR);
    if (ready < 0) fail("poll");
    std::array<std::uint8_t, protocol::header_size> bytes {};
    if (!read_exact(client_, bytes.data(), bytes.size(), true)) return false;
    const auto lengths = protocol::decode_header(bytes);
    frame.json.resize(lengths.json);
    frame.payload.resize(lengths.payload);
    read_exact(client_, frame.json.data(), frame.json.size());
    read_exact(client_, frame.payload.data(), frame.payload.size());
    return true;
}

/** Send an entire framed message, handling interrupted and partial writes.
 * @param frame Complete metadata/payload message to serialize and write in order.
 */
void UnixSocket::send(const protocol::Frame& frame) {
    // Framing remains independent of the socket and JSON command implementation.
    const auto bytes = protocol::header(frame);
    write_exact(client_, bytes.data(), bytes.size());
    write_exact(client_, frame.json.data(), frame.json.size());
    write_exact(client_, frame.payload.data(), frame.payload.size());
}
} // namespace analyzer::ipc
