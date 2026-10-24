// Copyright (c) 2026 vladg/sound
// SPDX-License-Identifier: MIT
#include "ui_launcher.h"
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <signal.h>
#include <spawn.h>
#include <stdexcept>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
extern char** environ;
namespace analyzer {
namespace fs = std::filesystem;
namespace {
fs::path executable_path() {
#ifdef __APPLE__
    uint32_t size = 0;
    _NSGetExecutablePath(nullptr, &size);
    std::vector<char> buffer(size);
    if (_NSGetExecutablePath(buffer.data(), &size) != 0)
        throw std::runtime_error("Cannot locate the application executable");
    return fs::canonical(buffer.data());
#else
    return fs::canonical("/proc/self/exe");
#endif
}
std::vector<std::string> ui_command(const std::string& entry_override) {
    const auto executable = executable_path();
    const auto environment_entry = std::getenv("VST3_ANALYZER_UI_ENTRYPOINT");
    const std::string entry = !entry_override.empty() ? entry_override : (environment_entry ? environment_entry : "");
    if (!entry.empty()) {
        if (!fs::is_regular_file(entry)) throw std::runtime_error("UI entry script does not exist: " + entry);
        const auto python = std::getenv("VST3_ANALYZER_PYTHON");
        return {python ? python : "python3", "-B", "-s", fs::absolute(entry).string()};
    }
    const auto mac_ui = executable.parent_path() / "VST3PluginAnalyzerUI";
    if (fs::is_regular_file(mac_ui)) return {mac_ui.string()};
    // Installed Linux layouts use entrypoint.py; source trees use the editable venv.
    std::vector<fs::path> roots;
    for (auto root = executable.parent_path(); !root.empty();) {
        roots.push_back(root);
        const auto parent = root.parent_path();
        if (parent == root) break;
        root = parent;
    }
    // Out-of-tree IDE builds still find their configured source environment.
    // Installed/bundled layouts above always take precedence over this fallback.
    roots.emplace_back(ANALYZER_SOURCE_ROOT);
    for (const auto& root : roots) {
        const bool installed = fs::is_regular_file(root / "entrypoint.py");
        const bool source = fs::is_regular_file(root / "pyproject.toml") && fs::is_directory(root / "python/vst3_analyzer");
        if (installed || source) {
            std::string python;
            if (const char* override = std::getenv("VST3_ANALYZER_PYTHON")) python = override;
            if (python.empty()) {
                std::ifstream marker(root / ".python-interpreter");
                std::getline(marker, python);
                if (!python.empty() && fs::path(python).is_relative() && fs::exists(root / python))
                    python = (root / python).string();
            }
            if (python.empty()) {
                for (const auto* environment : {".venv-macos-source", ".venv", "venv"}) {
                    const auto candidate = root / environment / "bin/python";
                    if (fs::exists(candidate)) { python = candidate.string(); break; }
                }
            }
            if (python.empty()) python = "python3";
            const auto entry = installed ? root / "entrypoint.py" : root / "packaging/common/entrypoint.py";
            return {python, "-B", "-s", entry.string()};
        }
    }
    throw std::runtime_error("Cannot locate the Python UI. Launch from an installed application or a prepared source checkout.");
}
}
UILauncher::UILauncher() {
    char pattern[] = "/tmp/v3pa-XXXXXX";
    const auto directory = ::mkdtemp(pattern);
    if (!directory) throw std::runtime_error("Cannot create application socket directory");
    directory_ = directory;
}
UILauncher::~UILauncher() {
    // A normal caller waits after closing the service; failure must not orphan a UI.
    if (running()) { ::kill(child_, SIGKILL); wait(); }
    ::unlink(socket_path().c_str());
    ::rmdir(directory_.c_str());
}
std::string UILauncher::socket_path() const { return directory_ + "/host.sock"; }
void UILauncher::start(int argc, char** argv) {
    std::string entry;
    std::vector<std::string> forwarded;
    for (int index = 1; index < argc; ++index) {
        if (std::string(argv[index]) == "--ui-entrypoint") {
            if (++index == argc) throw std::invalid_argument("--ui-entrypoint requires a Python entry script path");
            entry = argv[index];
        } else {
            forwarded.emplace_back(argv[index]);
        }
    }
    auto command = ui_command(entry);
    // Help and the state-free package check exit without connecting to this listener.
    bool standalone = false;
    for (const auto& argument : forwarded) {
        if (argument == "--help" || argument == "-h") {
            std::cout << "Native launcher: [--ui-entrypoint PATH] [PLUGIN] [UI options]\n"
                         "  --ui-entrypoint PATH  Use an explicit Python UI entry script.\n"
                         "  VST3_ANALYZER_PYTHON selects its Python interpreter.\n" << std::flush;
            standalone = true;
        }
        if (argument == "--bundle-check-report") standalone = true;
    }
    if (!standalone) {
        command.insert(command.end(), {"--connect-socket", socket_path(), "--host-pid", std::to_string(::getpid()),
                                       "--host", executable_path().string()});
    }
    command.insert(command.end(), forwarded.begin(), forwarded.end());
    std::vector<char*> arguments;
    for (auto& value : command) arguments.push_back(value.data());
    arguments.push_back(nullptr);
    const auto result = ::posix_spawnp(&child_, arguments.front(), nullptr, nullptr, arguments.data(), environ);
    if (result) { child_ = -1; throw std::runtime_error(std::string("Cannot start application UI: ") + std::strerror(result)); }
    ::setenv("VST3_ANALYZER_UI_PID", std::to_string(child_).c_str(), 1);
}
bool UILauncher::running() {
    if (child_ < 0) return false;
    const auto result = ::waitpid(child_, &status_, WNOHANG);
    if (result == child_ || (result < 0 && errno == ECHILD)) { child_ = -1; return false; }
    return true;
}
int UILauncher::wait() {
    if (child_ >= 0) {
        pid_t result;
        do { result = ::waitpid(child_, &status_, 0); } while (result < 0 && errno == EINTR);
        child_ = -1;
    }
    return WIFEXITED(status_) ? WEXITSTATUS(status_) : 1;
}
}
