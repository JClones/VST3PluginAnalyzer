// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
/**
 * @file
 * @brief Ownership and operations for private single-client Unix sockets used by the host service.
 */
#pragma once
#include "protocol/frame.h"
#include <string>

namespace analyzer::ipc {
/** Private single-client Unix-domain listener; removes only its own bound socket. */
class UnixSocket {
public:
    /** Bind a new socket path; refuse to overwrite an existing filesystem entry.
     * @param path New Unix-domain socket pathname to bind; an existing entry is never overwritten.
     */
    explicit UnixSocket(const std::string& path);
    /** Take ownership of an inherited connected AF_UNIX stream descriptor.
     * @param connected_fd Connected AF_UNIX stream descriptor whose ownership is transferred to this object.
     */
    explicit UnixSocket(int connected_fd);
    /** Close descriptors and unlink the socket if this object successfully bound it. */
    ~UnixSocket();
    /** Disallow copying this owner.
     * @param other Source instance; copying is forbidden because ownership is unique.
     */
    UnixSocket(const UnixSocket& other) = delete; ///< Descriptors have unique ownership.
    /** Disallow copy assignment.
     * @param other Source instance; assignment cannot copy its uniquely owned resources.
     */
    UnixSocket& operator=(const UnixSocket& other) = delete; ///< No implicit descriptor copies.
    /** Accept one client and apply bounded read/write timeouts. */
    void accept_client();
    /** Wait at most a bounded interval for frame data or disconnection.
     * @param timeout_ms Maximum poll wait in milliseconds; zero checks immediately and -1 waits indefinitely.
     */
    bool readable(int timeout_ms);
    /** Receive an exact framed message; false means EOF between complete frames.
     * @param frame Output message replaced with validated JSON metadata and optional binary payload.
     */
    bool receive(protocol::Frame& frame);
    /** Send all bytes, handling short writes and interrupted system calls.
     * @param frame Complete metadata/payload message to serialize and write in order.
     */
    void send(const protocol::Frame& frame);
private:
    std::string path_; ///< Owned socket filename, normally inside a private Python temp directory.
    int listener_ = -1; ///< Listening descriptor, closed on destruction.
    int client_ = -1; ///< Accepted client descriptor, closed on destruction.
    bool bound_ = false; ///< True only after successful bind, authorizing path cleanup.
};
} // namespace analyzer::ipc
