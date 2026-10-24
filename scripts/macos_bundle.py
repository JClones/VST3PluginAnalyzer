#!/usr/bin/env python3
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Prepare and verify Universal 2 application inputs without modifying the development environment."""
import argparse
from importlib import metadata, util
import json
import plistlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from zipfile import ZipFile

PROJECT = Path(__file__).resolve().parents[1]  # Source checkout, independent of the invoking directory.
SUPPORT = PROJECT / "build-macos-bundle"  # Generated icons, wheel cache, notices and packaging work.
HOST = PROJECT / "build-macos-universal/bin/Release/vst3-host-service"  # Separate universal C++ build output.
ARCHITECTURES = {"arm64", "x86_64"}  # Required slices in all bundled native components.
MACHO_MAGICS = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}  # Mach-O/fat headers.


def run(arguments, **kwargs):
    """Run command arguments with inherited output and raise on failure; kwargs configure subprocess.run."""
    print("Running:", " ".join(str(item) for item in arguments), flush=True)
    return subprocess.run([str(item) for item in arguments], check=True, **kwargs)


def is_macho(path):
    """Check the first four bytes of path without loading a potentially large native library."""
    with Path(path).open("rb") as stream:
        return stream.read(4) in MACHO_MAGICS


def macho_files(root):
    """Yield unique native files under root, following file links but rejecting links outside that tree."""
    root = Path(root).resolve()
    seen = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise RuntimeError(f"External file link inside package/bundle: {path} -> {resolved}")
        if resolved not in seen and is_macho(resolved):
            seen.add(resolved)
            yield resolved


def require_universal(path):
    """Require both architecture slices in native path and report any incompatible input precisely."""
    result = subprocess.run(["lipo", "-archs", str(path)], check=True, text=True, capture_output=True)
    found = set(result.stdout.split())
    if not ARCHITECTURES.issubset(found):
        raise RuntimeError(f"Universal 2 required: {path} contains {', '.join(sorted(found))}; need arm64 and x86_64")


def check_python():
    """Reject thin or free-threaded interpreters before creating the isolated bundle environment."""
    if sys.platform != "darwin":
        raise RuntimeError("macOS application packaging requires macOS")
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("The macOS 14.4 bundle profile requires standard Universal 2 Python 3.13")
    require_universal(Path(sys.executable).resolve())
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise RuntimeError("Use the standard CPython universal2 installer, not its free-threaded variant")
    print("Universal 2 Python executable verified.")


def check_environment():
    """Verify Python, NumPy, PySide6 and Shiboken native inputs before a universal freeze."""
    check_python()
    from PyInstaller.depend.bindepend import get_python_library_path

    library = get_python_library_path()
    if library is None:
        raise RuntimeError("Could not locate the Python shared library")
    require_universal(library)
    minimum = os.environ.get("MACOSX_DEPLOYMENT_TARGET", "14.4")
    require_minimum_os(library, minimum)
    count = 1
    for package in ("numpy", "PySide6", "shiboken6"):
        spec = util.find_spec(package)
        if spec is None or spec.origin is None:
            raise RuntimeError(f"Install the bundle environment dependencies first: {package} is missing")
        for path in macho_files(Path(spec.origin).parent):
            require_universal(path)
            require_minimum_os(path, minimum)
            count += 1
    print(f"Verified {count} universal native dependency files.")


def prepare_numpy(version=None, install=True):
    """Merge matching NumPy version wheels for both CPUs; install controls whether the current environment is updated."""
    if sys.platform != "darwin":
        raise RuntimeError("NumPy wheel merging requires macOS lipo tools")
    version = version or metadata.version("numpy")
    abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
    target_major = os.environ.get("MACOSX_DEPLOYMENT_TARGET", "14.4").split(".")[0]
    directory = SUPPORT / "wheels" / f"numpy-{version}-{abi}"
    wheels = []
    for architecture in ("arm64", "x86_64"):
        destination = directory / architecture
        destination.mkdir(parents=True, exist_ok=True)
        run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
             "--platform", f"macosx_{target_major}_0_{architecture}", "--python-version", abi[2:],
             "--implementation", "cp", "--abi", abi, "--dest", destination, f"numpy=={version}"])
        candidates = list(destination.glob(f"numpy-{version}-*.whl"))
        if len(candidates) != 1:
            raise RuntimeError(f"Expected one matching {architecture} NumPy wheel in {destination}")
        wheels.append(candidates[0])
    output = directory / "universal2"
    output.mkdir(exist_ok=True)
    run([sys.executable, "-m", "delocate.cmd.delocate_merge", *wheels, "-w", output])
    merged = list(output.glob(f"numpy-{version}-*universal2.whl"))
    if len(merged) != 1:
        raise RuntimeError("Wheel merge did not produce one Universal 2 NumPy wheel")
    # Verify the actual wheel contents, rather than trusting a universal2 filename/tag.
    with tempfile.TemporaryDirectory(prefix="v3pa-wheel-") as temporary:
        with ZipFile(merged[0]) as archive:
            archive.extractall(temporary)
        binaries = list(macho_files(temporary))
        if not binaries:
            raise RuntimeError("Merged NumPy wheel contains no native extensions")
        for path in binaries:
            require_universal(path)
    if install:
        if sys.prefix == sys.base_prefix:
            raise RuntimeError("Install merged NumPy only inside the isolated bundle virtual environment")
        run([sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", merged[0]])
    print(f"Universal NumPy ready: {merged[0]}")


def make_icon():
    """Render the existing vector artwork into standard macOS icon sizes using the installed Qt renderer."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    application = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(PROJECT / "python/vst3_analyzer/ui/assets/VST3_Icon.svg"))
    if not renderer.isValid():
        raise RuntimeError("Application vector icon could not be loaded")
    iconset = SUPPORT / "VST3_Icon.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for logical in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = logical * scale
            image = QImage(pixels, pixels, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            suffix = "@2x" if scale == 2 else ""
            if not image.save(str(iconset / f"icon_{logical}x{logical}{suffix}.png")):
                raise RuntimeError("Could not write application icon PNG")
    run(["iconutil", "-c", "icns", iconset, "-o", SUPPORT / "VST3_Icon.icns"])
    # Retain QApplication until all image operations have completed.
    return application


def collect_notices():
    """Copy available installed dependency notices, Python license and the bundled SDK license into build support."""
    destination = SUPPORT / "licenses"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("numpy", "PySide6-Essentials", "shiboken6", "pyqtgraph", "PyInstaller"):
        distribution = metadata.distribution(name)
        target = destination / name
        target.mkdir(exist_ok=True)
        (target / "METADATA.txt").write_text(distribution.read_text("METADATA") or "", encoding="utf-8")
        for entry in distribution.files or ():
            if any(word in str(entry).lower() for word in ("license", "copying", "notice")):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    # Preserve nested notice paths without accepting parent-directory escapes from package metadata.
                    relative = Path(*[part for part in Path(entry).parts if part not in ("..", ".")])
                    output = target / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, output)
    shutil.copyfile(PROJECT / "vst3sdk/LICENSE.txt", destination / "VST3-SDK-LICENSE.txt")
    # Retain notices for the SDK components and RapidJSON headers actually used by the native host.
    for relative in ("base/LICENSE.txt", "pluginterfaces/LICENSE.txt", "public.sdk/LICENSE.txt",
                     "vstgui4/vstgui/thirdparty/rapidjson/license.txt"):
        output = destination / "VST3-SDK" / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / "vst3sdk" / relative, output)
    # Include the application's own license and dependency/artwork attribution in released bundles.
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(PROJECT / name, destination / name)
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, destination / "PYTHON-LICENSE.txt")
    versions = {name: metadata.version(name) for name in
                ("vst3-plugin-analyzer", "numpy", "PySide6-Essentials", "shiboken6", "pyqtgraph", "PyInstaller", "delocate")}
    (SUPPORT / "build-info.json").write_text(json.dumps({"architectures": sorted(ARCHITECTURES),
                                                        "python": sys.version, "packages": versions}, indent=2) + "\n", encoding="utf-8")


def version_tuple(value):
    """Convert dotted macOS version value into an ordered three-component tuple."""
    parts = tuple(int(part) for part in value.split("."))
    return (parts + (0, 0, 0))[:3]


def require_minimum_os(path, minimum):
    """Reject a native path whose embedded deployment target exceeds the requested minimum macOS version."""
    # NumPy includes fat static archives for extension developers; these are not loadable runtime images.
    if Path(path).suffix == ".a":
        return
    build = subprocess.run(["vtool", "-show-build", str(path)], check=True, capture_output=True, text=True)
    for required in re.findall(r"\bminos\s+(\d+(?:\.\d+)+)", build.stdout):
        if version_tuple(required) > version_tuple(minimum):
            raise RuntimeError(f"{path} requires macOS {required}, newer than the bundle target {minimum}")


def verify_bundle(bundle, minimum="14.4"):
    """Audit bundle architecture, external link dependencies and minimum OS requirements before distribution."""
    bundle = Path(bundle).resolve()
    native = list(macho_files(bundle))
    if not native:
        raise RuntimeError(f"No native binaries found in {bundle}")
    for path in native:
        require_universal(path)
        result = subprocess.run(["otool", "-L", str(path)], check=True, capture_output=True, text=True)
        for line in result.stdout.splitlines():
            # Headers and architecture labels are unindented; dependency paths precede compatibility/version details.
            if not line.startswith("\t"):
                continue
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith("/") and not dependency.startswith(("/usr/lib/", "/System/Library/")):
                raise RuntimeError(f"External runtime dependency: {path}: {dependency}")
        require_minimum_os(path, minimum)
    run(["codesign", "--verify", "--deep", "--strict", bundle])
    print(f"Verified {len(native)} Universal 2 bundle binaries; no external non-system library paths.")


def build():
    """Build the dual-architecture host, bundle the application, audit it and run a state-free smoke check."""
    check_environment()
    minimum = os.environ.get("MACOSX_DEPLOYMENT_TARGET", "14.4")
    run(["cmake", "-S", PROJECT, "-B", PROJECT / "build-macos-universal", "-DCMAKE_BUILD_TYPE=Release",
         "-DCMAKE_OSX_ARCHITECTURES=arm64;x86_64", f"-DCMAKE_OSX_DEPLOYMENT_TARGET={minimum}", "-DBUILD_TESTING=OFF"])
    run(["cmake", "--build", PROJECT / "build-macos-universal", "--config", "Release",
         "--target", "vst3-host-service", "--parallel", "4"])
    require_universal(HOST)
    SUPPORT.mkdir(parents=True, exist_ok=True)
    application = make_icon()
    collect_notices()
    environment = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=minimum)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--workpath", SUPPORT / "pyinstaller",
         "--distpath", PROJECT / "dist", PROJECT / "packaging/macos/VST3PluginAnalyzer.spec"], env=environment)
    bundle = PROJECT / "dist/VST3 Plugin Analyzer.app"
    # The IDE/LaunchServices entry point is native; PyInstaller's UI remains a sibling.
    shutil.copy2(HOST, bundle / "Contents/MacOS/VST3 Plugin Analyzer")
    info = bundle / "Contents/Info.plist"
    with info.open("rb") as stream:
        values = plistlib.load(stream)
    values["CFBundleExecutable"] = "VST3 Plugin Analyzer"
    with info.open("wb") as stream:
        plistlib.dump(values, stream)
    # PyInstaller re-signs collected binaries; apply host-specific entitlements
    # afterwards, then seal the outer bundle again without changing nested code.
    identity = os.environ.get("VST3_ANALYZER_SIGN_IDENTITY") or "-"
    signing = ["codesign", "--force", "--sign", identity]
    if identity != "-":
        signing.extend(["--options", "runtime", "--timestamp"])
    run([*signing, "--entitlements",
         PROJECT / "packaging/macos/host.entitlements.plist",
         bundle / "Contents/Frameworks/native/vst3-host-service"])
    run([*signing, "--entitlements", PROJECT / "packaging/macos/host.entitlements.plist", bundle])
    verify_bundle(bundle, minimum)
    report = SUPPORT / "bundle-check.json"
    report.unlink(missing_ok=True)
    # Running from / proves no accidental dependence on the source checkout's working directory.
    # A clean launch must not find the extracted build-time Python or Qt through inherited environment paths.
    smoke_environment = {key: value for key, value in os.environ.items()
                         if not key.startswith("DYLD_") and key not in
                         ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")}
    smoke_environment["QT_QPA_PLATFORM"] = os.environ.get("VST3_ANALYZER_SMOKE_QPA", "cocoa")
    run([bundle / "Contents/MacOS/VST3 Plugin Analyzer", "--bundle-check-report", report],
        cwd="/", env=smoke_environment, timeout=60)
    if not json.loads(report.read_text(encoding="utf-8")).get("ok"):
        raise RuntimeError(f"Bundled application smoke check failed: {report}")
    signature = "ad-hoc signed" if identity == "-" else "Developer ID signed"
    print(f"Application prepared ({signature}; notarization remains separate): {bundle}")
    return application


def main():
    """Dispatch the requested packaging preparation, validation or build command."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-python")
    commands.add_parser("check-environment")
    commands.add_parser("build")
    wheel = commands.add_parser("prepare-numpy")
    wheel.add_argument("--numpy-version")
    wheel.add_argument("--no-install", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--minimum", default="14.4")
    args = parser.parse_args()
    try:
        if args.command == "check-python":
            check_python()
        elif args.command == "check-environment":
            check_environment()
        elif args.command == "prepare-numpy":
            prepare_numpy(args.numpy_version, install=not args.no_install)
        elif args.command == "verify":
            verify_bundle(args.bundle, args.minimum)
        else:
            build()
    except (RuntimeError, OSError, subprocess.CalledProcessError, metadata.PackageNotFoundError) as error:
        parser.exit(1, f"Bundle preparation failed: {error}\n")


if __name__ == "__main__":
    main()
