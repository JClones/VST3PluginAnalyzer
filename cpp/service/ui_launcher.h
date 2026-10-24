// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
#pragma once
#include <string>
#include <sys/types.h>

namespace analyzer {
/** Own the private listener directory and the UI child of a host-first launch. */
class UILauncher {
public:
    UILauncher();
    ~UILauncher();
    std::string socket_path() const;
    void start(int argc, char** argv);
    bool running();
    int wait();
private:
    std::string directory_;
    pid_t child_ = -1;
    int status_ = 0;
};
}
