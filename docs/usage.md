# Usage guide

## Loading plugins and controlling them

Use **Plugin → Load VST3...** to select an installed plugin, or choose one of ten
entries under **Recent**. **Unload** releases it. External sidechain inputs are
silenced; the test signal is sent to the main input bus. **Soft Bypass** requests
the plugin's own bypass parameter while processing continues, when supported.

**Window → Default Editor** opens the plugin's native GUI. **Parameter List**
opens the analyzer's floating, scrollable parameter controls. Enter logical values
such as a frequency directly in the value field; the plugin interprets the text.
Double-click or Alt-click a slider to reset its declared default value. Two-state
parameters use checkboxes. **Program → Load... / Export...** use `.vstpreset`
files; factory programs appear below those commands when supplied by the plugin.

## Measurements

New sessions start in **Frequency Response**. Existing sessions restore the last
selected measurement.

| Mode | Input / result |
| --- | --- |
| Frequency Response | Repeated impulses; magnitude, phase, and group delay from the impulse response |
| Harmonic Response | Continuous bin-centered sine; spectrum plus THD, THD+N, and Klirr |
| Impulse Response | Impulse waveform in milliseconds; reported latency is retained at negative times |
| White / Pink Noise | Uncorrelated stereo noise and an output spectrum; no distortion readout |
| Silence | Silent input and an output spectrum; no distortion readout |
| Performance | Three runs of one second of noise followed by one second of impulse samples, with each native processing time logged separately |

Frequency and harmonic measurements compensate for reported plugin latency;
impulse response keeps it visible. The harmonic sine is aligned to an FFT bin,
so the actual frequency can differ slightly from the requested value. Time-varying
plugins may need time to settle after changing parameters.

**Window → Settings...** controls sample rate, FFT size, maximum processing block
size, and the harmonic test's requested sine frequency and peak level. The actual
sine is rounded to the nearest FFT bin to avoid windowing. Defaults are 44,100 Hz,
8,192 samples, 1,000 Hz, and 0 dBFS. Precision is automatic: float64 plugin I/O if
supported, float32 otherwise, with float64 analysis and IPC. **Apply** updates the
measurement and latency display without closing the dialog.

## Plot navigation and display

Plugins run in a separate process, so analyzer shortcuts do not work while the
plugin editor window is active: those keystrokes do not reach VST3 Plugin
Analyzer. Activate an analyzer window before using its shortcuts.

- Drag from top left to bottom right to box-zoom.
- Drag from bottom right to top left to reset both axes.
- Drag from bottom left to top right to reset Y; from top right to bottom left to reset X.
- Right-drag or two-finger scroll to pan. Pinch to zoom both axes.
- Shift constrains zoom to Y; Alt constrains zoom to X. The same modifiers apply
  to the Zoom In/Out keyboard commands.
- **Zoom Full** fits the data, using at least the default vertical span.
- **Show Cursor Values** displays a dashed crosshair and current trace values.

Default frequency bounds start at the next multiple of 5 Hz strictly above FFT
bin one and end at Nyquist. Harmonic magnitude defaults to approximately 0 to
−200 dB, frequency-response magnitude to −20 to +20 dB, impulse response to −1
to +1. Wrapped phase is bounded by ±180°. Plot values below −300 dB are shown as
−∞ dB by cursor inspection.

Identical stereo output is magenta; different left/right output is blue/red.
**Plot → M/S Mode** enables Mid/Side inspection. Frequency and impulse responses
use separate Mid and Side excitations; harmonic response uses equal L/R excitation
and decodes the output to Mid/Side.

**Store** keeps the current response behind the live plot, across frequency-response
view types; repeat to accumulate comparisons. **Clear** removes stored responses.
**Make Reference** captures the current frequency response as the baseline for
differences, including phase and group delay; **Clear Reference** removes it. Stored curves are cleared when FFT size
changes and are not saved between sessions.

**Export...** offers PNG, SVG, or CSV. CSV includes a single frequency column and
main/stored traces for the current view, or time/sample values in impulse mode.

## Shortcuts

| Action | Shortcut |
| --- | --- |
| Load plugin | Cmd+O on macOS / Ctrl+O on Linux |
| Show/hide Default Editor | F2 |
| Show/hide Parameter List | F3 |
| Frequency Response | Cmd+F / Ctrl+F |
| Harmonic Response | Cmd+H / Ctrl+H |
| Performance | Cmd+P / Ctrl+P |
| Magnitude / wrapped phase / unwrapped phase / group delay | M / P / U / G |
| Store / Clear | F8 / F7 |
| Make Reference / Clear Reference | F6 / F5 |
| Zoom In / Out | Cmd+plus / Cmd+minus on macOS; Ctrl on Linux |
| Zoom Default | Cmd+0 on macOS / Ctrl+0 on Linux |

## Saved state and troubleshooting

Settings and `.vstpreset` state sidecars are in
`~/.config/VST3 Plugin Analyzer/`, shared between source and bundle versions.
You can run multiple instances; each connects to its own plugin host. The first
instance saves preferences and the session. Additional instances start with the
current preferences and no plugin loaded, and their session changes are temporary.
A clean close saves the first instance’s plugin state and window layout.
After a crash or force-quit the next launch keeps preferences but skips automatic
plugin loading. Load the plugin explicitly to try again.

A lost connection or timeout can indicate that the plugin host stopped or became busy.
Try unloading and reloading the plugin, or restart the analyzer. On Linux, plugin
editors require X11 or XWayland, plus the libraries required by the plugin vendor.

When reporting an issue, include the analyzer version, OS, CPU architecture,
plugin name/version and steps to reproduce. No audio-device output is provided.
