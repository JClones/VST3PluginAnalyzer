# Third-party components

The project's MIT license covers original VST3 Plugin Analyzer code by
vladg/sound. Third-party code, libraries, and artwork retain their own terms.

| Component | Role | License / notices |
| --- | --- | --- |
| [VST3 SDK](https://github.com/steinbergmedia/vst3sdk) | Native host interfaces and hosting code | MIT; retain Steinberg's SDK and individual component notices |
| Python | GUI runtime | Python Software Foundation license and bundled notices |
| NumPy | Float64 signal processing and FFT | BSD and bundled third-party notices |
| PySide6 / Shiboken / Qt | Desktop UI | LGPLv3/GPLv3 or commercial terms, depending on component; see installed distribution notices |
| pyqtgraph | Plot rendering | MIT |
| PyInstaller bootloader | Starts the bundled Python runtime | GPL with the PyInstaller distribution exception; see collected license notices |

The SDK is fetched separately at a pinned revision; its license is not replaced
by this project's license. Test fixtures compile unmodified Steinberg example
sources and retain their authorship. Python dependencies are installed from their
upstream distributions. The macOS bundle collects available license files and
distribution metadata in `Contents/Resources/licenses/` and package versions in
`Contents/Resources/build-info.json` (resource locations may be linked into
`Contents/Frameworks` by PyInstaller). It also includes this project's license
and these notices.

Linux tar, Debian and RPM distributions ship application Python sources and the
compiled native host, with host SDK notices under `runtime/licenses` (tar) or
the application directory's `licenses` (system packages). They do not bundle
Python, NumPy, PySide6/Qt, pyqtgraph or the PyInstaller bootloader. Those runtime
dependencies are installed separately and retain their own distribution notices.

The application icon is derived from Steinberg VST artwork supplied for this
project. The recolored SVG and ICO icons retain the original VST artwork geometry.
VST and related logos are Steinberg trademarks. The MIT license does not grant
trademark rights or relicense that artwork. This is an independent project.
