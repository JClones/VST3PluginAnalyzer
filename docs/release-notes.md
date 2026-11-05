# VST3 Plugin Analyzer v0.3.1 — Analysis and usability improvements

This update adds DC response inspection, phase fading based on signal level, and
independent input-channel selection, alongside plugin editor and settings fixes.

- **DC response:** a marker and dashed connector show the zero-Hz response at the
  left plot edge. Show Cursor Values reports DC at that horizontal position,
  regardless of mouse height. The connector remains visible when DC is outside
  the vertical range or the first bin is off-screen to the right.
- **Phase and group delay:** curves fade according to the original response
  level as they approach the calculation threshold. Reference comparisons use
  the weaker original response; stored curves and exports preserve the fade.
- **Channels and M/S:** M/S Mode is a separate checkbox. The Channels submenu
  selects Left and right, Left only, or Right only independently of M/S mode.
  One-sided stereo measurements retain both outputs for crosstalk inspection.
- **Plugin editors:** restoring a closed editor no longer constructs its view.
  Plugins without an editor disable F2 and open the parameter list on initial load.
- **Settings:** the phase threshold ranges from −200 to −40 dB. You can clear and
  retype its value; range checking occurs when editing finishes or Apply/OK is used.
- **Zoom Full:** frequency-response magnitude now stops at −200 dB, matching the
  harmonic-response limit.
- **About:** Window → About... shows the application version and GitHub link.

## Brief description

Adds DC response inspection, phase fading based on original signal levels, and
independent channel selection, with fixes for plugin editors, threshold editing,
and frequency-response zoom.
