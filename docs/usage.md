# Usage guide

## Loading plugins and controlling them

Use **Plugin → Load VST3...** to select an installed plugin, or choose one of ten
entries under **Recent**. **Unload** releases it. External sidechain inputs are
silenced; the test signal is sent to the main input bus. **Soft Bypass** requests
the plugin's own bypass parameter while processing continues, when supported.

**Window → Default Editor** (**F2**) opens or closes the plugin's native GUI.
Closing it destroys the native window and plugin view; reopening constructs a
fresh editor at the last window geometry while audio processing continues.
**Parameter List** opens the analyzer's floating, scrollable parameter controls.
For plugins without a native editor, F2 is disabled and the parameter list opens
automatically on load. Saved sessions retain your parameter-window visibility.
Enter logical values such as a frequency directly in the value field; the plugin interprets the text.
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
8,192 samples, 1,000 Hz, and 0 dBFS. The FFT size dropdown offers powers of two
from 1,024 to 65,536 samples. Precision is automatic: float64 plugin I/O if
supported, float32 otherwise, with float64 analysis and IPC. **Apply** updates the
measurement and latency display without closing the dialog.

Below **Harmonic Response Test**, the **Frequency Response Test** section contains
**Phase calculation threshold**, adjustable from −200 to −40 dB (default −160 dB).
Raise it to exclude more of a noisy stopband. Changing only this threshold keeps
audio processing and capture history running, and updates live, stored and
reference plots without discarding their measurements.

## Phase and group delay

Phase analysis treats magnitudes below the configured threshold as unknown, without changing the
magnitude measurement or its display. An isolated missing bin or a longer gap
bounded by valid phase is interpolated along the shortest phase arc. These
estimates use faint dashed lines in wrapped/unwrapped phase, as do phase-wrap
connectors. Group delay is calculated directly from the captured impulse response,
using `Re{FFT((n−L)·h[n]) / FFT(h[n])}` in samples, where `L` is reported
latency, rather than differentiating phase.
Reference plots subtract the reference's delay. Bounded gaps in direct delay are
interpolated separately and dashed too. Missing leading or trailing ranges are omitted,
not extrapolated. At a true spectral zero, interpolated phase and delay remain
estimates rather than measured values.

Frequency-response acquisition retains the complete latency prefix, including
pre-ringing, and the output during the silent recovery interval. Reported latency
is compensated mathematically in phase and group delay; no prefix is cut off.
The selected FFT size determines the frequency grid; the captured record includes
twice that many post-zero samples to retain more of the impulse tail. All recorded
samples contribute to the analysis. Longer FFT sizes can still be needed for very
long responses, especially when measuring group delay in a deeply attenuated band.

Unwrapping is anchored at the stronger response edge (the low-frequency edge wins
near ties), so a near-zero high-pass stopband does not set the phase branch.
Unwrapped phase has an arbitrary whole-turn offset; that offset does not affect
group delay. Noise above the selected threshold is still included: neither the
threshold nor the direct-delay method recovers information lost to float32 errors
or an insufficient capture length. Spectrum-only responses without a captured
impulse retain phase differentiation as a fallback.

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
- **Zoom Full** fits the data, using at least the default vertical span. Harmonic
  and frequency-response magnitude views stop at −200 dB; manual zoom remains unrestricted.
- **Show Cursor Values** displays a dashed crosshair and current trace values.

Default frequency bounds start at the next multiple of 5 Hz strictly above FFT
bin one and end at Nyquist. Harmonic magnitude defaults to approximately 0 to
−200 dB, frequency-response magnitude to −20 to +20 dB, impulse response to −1
to +1. Wrapped phase is bounded by ±180°. Plot values below −300 dB are shown as
−∞ dB by cursor inspection.

Identical stereo output is magenta; different left/right output is blue/red.
**Plot → M/S Mode** is a checkbox switching between Left/Right and Mid/Side
output analysis. **Plot → Channels** independently selects the test inputs:

- **Left and right** (default) feeds both inputs in stereo mode. For M/S Frequency
  Response and Impulse Response, it alternates Mid-only and Side-only impulses,
  measuring M→M and S→S separately. Those two plots may briefly be out of sync
  after parameter changes.
- **Left only** feeds only the left input; both outputs are measured.
- **Right only** feeds only the right input; both outputs are measured.

One-sided stereo excitation exposes crosstalk into the other output. In M/S
Frequency Response and Impulse Response, one-sided captures are normalized to the
injected components: Left only uses Mid=L+R and Side=L−R; Right only uses Mid=L+R
and Side=R−L. An unchanged signal path therefore reads 0 dB in both frequency
plots, with correct phase. These captures include M/S cross-conversion rather
than isolating M→M and S→S. Channel selection also routes the harmonic tone and
signal generators; their M/S outputs use the usual (L+R)/2 and (L−R)/2 decoding.
Changing either control clears stored curves and references. Both choices are
saved independently with the session.
Harmonic response retains equal L/R tone input, and generator signals are unchanged;
both decode their output to Mid/Side as before.

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
| Open/close Default Editor | F2 |
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

If a request takes more than ten seconds, a line above the plot offers
**Exit application**. You can keep waiting indefinitely, including while the host
is paused in a debugger. The line disappears when processing resumes. Choosing
**Exit application** forces a dirty exit. If the host crashes or exits, the
interface closes automatically; it does not restart the host.

A normal close saves plugin state, waits for the host to finish and marks the
session clean only if both succeed. Forced exits leave the session dirty, so
the next launch restores preferences without automatically loading the plugin.
See the [debugging guide](debugging.md) to launch the native executable in an IDE.
On Linux, plugin editors require X11 or XWayland, plus the libraries required by
the plugin vendor.

When reporting an issue, include the analyzer version, OS, CPU architecture,
plugin name/version and steps to reproduce. No audio-device output is provided.
