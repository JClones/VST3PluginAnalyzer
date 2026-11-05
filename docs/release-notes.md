# VST3 Plugin Analyzer v0.3.0 — Measurement and workflow improvements

Frequency-response measurements now retain pre-ringing and the impulse tail during
recovery, improving magnitude, phase and group-delay accuracy for plugins with
reported latency. Group delay is calculated directly from the captured impulse
response with latency compensation.

- Added a configurable **Phase calculation threshold** in Settings. Phase and
  group delay below the threshold are omitted at the response edges; bounded gaps
  use dashed interpolation. Magnitude measurements remain available below it.
- Added independent **M/S Mode** and **Channels** controls. Left-only and
  right-only excitation works with stereo or M/S analysis, including stereo
  crosstalk measurements.
- Plugin editor views are created only when opened. Closing destroys the window
  and view; reopening creates a fresh editor.
- Plugins without a native editor open the parameter list automatically, with
  the default editor command disabled.
- Added units to values in the F3 parameter window. Values select automatically
  on focus, and background refreshes preserve selection and unfinished edits.
- FFT size is now selected from powers of two between 1024 and 65536.
- Improved phase grid spacing and reordered the Plugin and Plot menus.
- Added a DC marker and zero-Hz cursor readout when the viewport includes the
  interval leading up to the first frequency-response bin.

## Brief description

Improved frequency-response accuracy with complete pre-ringing capture, direct
group-delay analysis, configurable phase thresholds, independent channel and M/S
controls, and smoother parameter editing.
