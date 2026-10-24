# Debugging plugins

Launch the native analyzer executable under your IDE's debugger. It starts the
Python interface and hosts the plugin in the process being debugged, so plugin
loading and initialization can be debugged from the start. No attach step or PID
file is needed. You can select the plugin in the interface or pass its `.vst3`
bundle path as a program argument.

The normal application launchers use this same startup order. Each instance has
its own private Unix socket and UI.

## Executables

- Linux installation: `vst3-plugin-analyzer` in the installation's `bin` directory
  resolves to the native host. Its runtime executable is
  `lib/vst3-plugin-analyzer/native/vst3-host-service` under the installation prefix.
- macOS application: `VST3 Plugin Analyzer.app/Contents/MacOS/VST3 Plugin Analyzer`.
- Source build: `build/bin/Release/vst3-host-service`, or
  `build-macos-source/bin/Release/vst3-host-service` for the macOS source setup.

Prepare the Python environment using the source build instructions before
launching a source executable. Out-of-tree builds can also locate the configured
source checkout's environment.

For native host debug symbols, configure a separate build:

```sh
cmake -S . -B build-debug -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=OFF
cmake --build build-debug --target vst3-host-service
```

Select `build-debug/bin/Debug/vst3-host-service` as the debugger's program.
Build your plugin with debug symbols too, and set the desired plugin breakpoints
in your IDE. For multi-configuration generators, build with `--config Debug`.

## Custom UI location

Use `--ui-entrypoint` to specify a Python UI entry script explicitly:

```sh
/path/to/vst3-host-service \
  --ui-entrypoint /path/to/VST3PluginAnalyzer/packaging/common/entrypoint.py \
  "/path/to/Your Plugin.vst3"
```

Set `VST3_ANALYZER_PYTHON` in the debugger's environment to select the interpreter,
for example `/path/to/VST3PluginAnalyzer/.venv/bin/python`. It must have the
application's GUI dependencies installed. The command-line entrypoint takes
precedence over the optional `VST3_ANALYZER_UI_ENTRYPOINT` environment variable.
The usual layout is discovered automatically when no override is provided.

## Breakpoints and exit

The interface waits indefinitely for a request, including while the debugger has
paused the host. After ten seconds, a line above the plot says
“The plugin is taking longer than expected to respond.” You can continue waiting
or choose **Exit application**. The line disappears when the request completes.

Closing the analyzer normally saves plugin state and waits for native shutdown.
Only successful state saving and shutdown authorize automatic plugin restoration
on the next launch. **Exit application**, a plugin crash, or stopping the native
process in the debugger leaves a dirty session: the interface exits and the next
launch restores preferences without automatically loading the plugin.

## UI-first startup

The original startup order remains available explicitly:

```sh
.venv/bin/python -m vst3_analyzer.ui --host /path/to/vst3-host-service
```

On macOS source installations, use `.venv-macos-source/bin/python` instead.
The same wait banner and clean/dirty exit rules apply in both startup modes.
