#!/usr/bin/env python3
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Create lightweight Linux distributions using system Python/Qt dependencies."""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

PROJECT = Path(__file__).resolve().parents[1]  # Source checkout containing the SDK.
WORK = PROJECT / 'build-linux-bundle'  # Disposable release staging and build files.
NAME = 'vst3-plugin-analyzer'  # Stable launcher and package identifier.
ASSETS = PROJECT / 'packaging/linux'  # Maintained desktop and installation assets.


def run(arguments, **kwargs):
    """Run arguments as a checked subprocess; kwargs supply optional subprocess settings."""
    subprocess.run([str(value) for value in arguments], check=True, **kwargs)


def copy(source, destination):
    """Copy source to destination, creating the destination's parent directories."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def tar_owner(member):
    """Normalize member's archive ownership instead of leaking the build user's uid/name."""
    member.uid = member.gid = 0
    member.uname = member.gname = 'root'
    return member


def package(version, host, output):
    """Package the same application sources/native host in all three formats."""
    machine = platform.machine()
    deb_arch, rpm_arch = {'x86_64': ('amd64', 'x86_64'), 'aarch64': ('arm64', 'aarch64')}[machine]
    # A newer build host must not advertise the older Ubuntu 22.04 runtime floor.
    libc_floor = platform.libc_ver()[1]
    stdcpp_floor = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${Version}', f'libstdc++6:{deb_arch}'], text=True).strip().split('-', 1)[0]
    stem = f'{NAME}-{version}-linux-{machine}'
    stage = WORK / 'tar' / stem
    if stage.parent.exists():
        shutil.rmtree(stage.parent)
    stage.mkdir(parents=True)
    # The source tar stays runnable with an external Python before and after installation.
    tar_runtime = stage / 'runtime'
    tar_runtime.mkdir()
    shutil.copytree(PROJECT / 'python/vst3_analyzer', tar_runtime / 'python/vst3_analyzer',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', 'README.md'))
    # Only the native host is compiled; Python and third-party dependencies are external.
    copy(host, tar_runtime / 'native/vst3-host-service')
    copy(ASSETS / 'source-launcher.sh', tar_runtime / NAME)
    copy(ASSETS / 'check-dependencies.py', tar_runtime / 'check-dependencies.py')
    copy(ASSETS / 'runtime-requirements.txt', tar_runtime / 'requirements.txt')
    copy(PROJECT / 'packaging/common/entrypoint.py', tar_runtime / 'entrypoint.py')
    for relative in ('LICENSE.txt', 'base/LICENSE.txt', 'pluginterfaces/LICENSE.txt',
                     'public.sdk/LICENSE.txt', 'vstgui4/vstgui/thirdparty/rapidjson/license.txt'):
        copy(PROJECT / 'vst3sdk' / relative, tar_runtime / 'licenses/VST3-SDK' / relative)
    for name in ('install.sh', 'uninstall.sh', f'{NAME}.desktop'):
        copy(ASSETS / name, stage / name)
    copy(PROJECT / 'python/vst3_analyzer/ui/assets/VST3_Icon.svg', stage / f'{NAME}.svg')
    (stage / 'README').write_text(
        (ASSETS / 'README.in').read_text(encoding='utf-8').replace('@VERSION@', version)
        .replace('@ARCH@', machine).replace('@GLIBC@', libc_floor).replace('@STDCPP@', stdcpp_floor),
        encoding='utf-8')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        copy(PROJECT / name, stage / name)
    with tarfile.open(output / f'{stem}.tar.gz', 'w:gz', compresslevel=6) as archive:
        archive.add(stage, arcname=stem, filter=tar_owner)
    # Validate source imports, real plot rendering and host IPC with the build interpreter.
    run([sys.executable, tar_runtime / 'check-dependencies.py'])
    run([tar_runtime / NAME, '--bundle-check-report', WORK / 'smoke.json'], cwd='/',
        env=dict(os.environ, VST3_ANALYZER_PYTHON=sys.executable,
                 QT_QPA_PLATFORM='offscreen', DBUS_SESSION_BUS_ADDRESS='unix:path=/nonexistent'))
    # A stable /usr/local/bin symlink avoids embedding the build checkout in launchers.
    root = WORK / 'root'
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(tar_runtime, root / 'usr/local/lib' / NAME, symlinks=True)
    (root / 'usr/local/lib' / NAME / '.python-interpreter').write_text('/usr/bin/python3\n')
    (root / 'usr/local/bin').mkdir(parents=True)
    (root / 'usr/local/bin' / NAME).symlink_to(f'../lib/{NAME}/{NAME}')
    copy(ASSETS / f'{NAME}.desktop', root / 'usr/local/share/applications' / f'{NAME}.desktop')
    copy(stage / f'{NAME}.svg', root / 'usr/local/share/pixmaps' / f'{NAME}.svg')
    desktop = root / 'usr/local/share/applications' / f'{NAME}.desktop'
    desktop.write_text(desktop.read_text().replace(f'Exec={NAME}', f'Exec=/usr/local/bin/{NAME}')
                       .replace(f'Icon={NAME}', f'Icon=/usr/local/share/pixmaps/{NAME}.svg'), encoding='utf-8')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        copy(PROJECT / name, root / 'usr/local/share/doc' / NAME / name)
    # APT resolves Python/Qt dependencies; NumPy uses Debian's epoch 1.
    control = root / 'DEBIAN'
    control.mkdir()
    (control / 'control').write_text(f'''Package: {NAME}
Version: {version}
Section: sound
Priority: optional
Architecture: {deb_arch}
Maintainer: vladg/sound
Installed-Size: {sum(p.lstat().st_size for p in root.rglob('*') if p.is_file() or p.is_symlink()) // 1024}
Depends: libc6 (>= {libc_floor}), libstdc++6 (>= {stdcpp_floor}), libgcc-s1, libx11-6, python3 (>= 3.10), python3-numpy (>= 1:2.0), python3-numpy (<< 1:3), python3-pyqtgraph (>= 0.13.7), python3-pyqtgraph (<< 1), python3-pyside6.qtcore (>= 6.8), python3-pyside6.qtcore (<< 7), python3-pyside6.qtgui (>= 6.8), python3-pyside6.qtgui (<< 7), python3-pyside6.qtwidgets (>= 6.8), python3-pyside6.qtwidgets (<< 7), python3-pyside6.qtsvg (>= 6.8), python3-pyside6.qtsvg (<< 7), python3-pyside6.qtopengl (>= 6.8), python3-pyside6.qtopengl (<< 7), python3-pyside6.qtopenglwidgets (>= 6.8), python3-pyside6.qtopenglwidgets (<< 7), python3 (>= 3.12) | python3-pyside6.qtcore (<< 6.12)
Description: VST3 audio plugin measurement and analysis
 Isolated native plugin host with a Python/Qt measurement interface.
 Uses distribution Python, NumPy and PySide6/Qt packages.
 Linux VST3 plugins are installed separately.
''', encoding='utf-8')
    # Refresh optional desktop caches during install, upgrade, removal and purge.
    hook = '''#!/bin/sh
set -e
if [ -d /usr/local/share/applications ] && command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database /usr/local/share/applications || true; fi
exit 0
'''
    for name in ('postinst', 'postrm'):
        (control / name).write_text(hook, encoding='utf-8')
        (control / name).chmod(0o755)
    # Keep package-manager ownership separate from manually installed system tarballs.
    preinstall = control / 'preinst'
    preinstall.write_text("#!/bin/sh\nset -e\nif [ -f /usr/local/lib/vst3-plugin-analyzer/.analyzer-install ]; then\n"
                          "  echo 'Uninstall the system tar installation before installing this package.' >&2\n"
                          "  exit 1\nfi\n"
                          "if [ -f /usr/lib/vst3-plugin-analyzer/.analyzer-source-install.json ]; then\n"
                          "  echo 'Uninstall the source installation before installing this package.' >&2\n"
                          "  exit 1\nfi\n", encoding='utf-8')
    preinstall.chmod(0o755)
    run(['dpkg-deb', '-Zgzip', '-z6', '--build', '--root-owner-group', root, output / f'{NAME}_{version}_{deb_arch}.deb'])
    shutil.rmtree(control)
    # RPM owns the same staged files. Disable private-library dependency/provide generation and stripping.
    top = WORK / 'rpm'
    top.mkdir(exist_ok=True)
    spec = top / 'analyzer.spec'
    rpm_requires = rpm_dependencies(tar_runtime / 'native')
    spec.write_text(f'''%global __os_install_post %{{nil}}
%global _build_id_links none
%global _binary_payload w6.gzdio
Name: {NAME}
Version: {version}
Release: 1
Summary: VST3 audio plugin measurement and analysis
License: MIT
BuildArch: {rpm_arch}
AutoReqProv: no
Requires: glibc >= {libc_floor}, {', '.join(rpm_requires)}
Requires: python3 >= 3.10
Requires: python3dist(numpy) >= 2, python3dist(numpy) < 3
Requires: python3dist(pyside6) >= 6.8, python3dist(pyside6) < 7
Requires: python3dist(pyqtgraph) >= 0.13.7, python3dist(pyqtgraph) < 1
Requires: (python3 >= 3.12 or python3dist(pyside6) < 6.12)
%description
Isolated VST3 host and Qt measurement interface using distribution Python, NumPy,
PySide6 and pyqtgraph packages.
%pre
if [ -f /usr/local/lib/{NAME}/.analyzer-install ]; then
    echo 'Uninstall the system tar installation before installing this package.' >&2
    exit 1
fi
if [ -f /usr/lib/{NAME}/.analyzer-source-install.json ]; then
    echo 'Uninstall the source installation before installing this package.' >&2
    exit 1
fi
%prep
%build
%install
mkdir -p "%{{buildroot}}"
cp -a "{root}/." "%{{buildroot}}/"
%post
[ -d /usr/local/share/applications ] && command -v update-desktop-database >/dev/null && update-desktop-database /usr/local/share/applications || :
%postun
[ -d /usr/local/share/applications ] && command -v update-desktop-database >/dev/null && update-desktop-database /usr/local/share/applications || :
%files
%defattr(-,root,root,-)
/usr/local/lib/{NAME}
/usr/local/bin/{NAME}
/usr/local/share/applications/{NAME}.desktop
/usr/local/share/pixmaps/{NAME}.svg
/usr/local/share/doc/{NAME}
''', encoding='utf-8')
    run(['rpmbuild', '-bb', '--define', f'_topdir {top}', spec])
    rpm = top / 'RPMS' / rpm_arch / f'{NAME}-{version}-1.{rpm_arch}.rpm'
    copy(rpm, output / rpm.name)
    # Checksums cover only the artifacts produced by this invocation's architecture/version.
    artifacts = [output / f'{stem}.tar.gz', output / f'{NAME}_{version}_{deb_arch}.deb',
                 output / f'{NAME}-{version}-1.{rpm_arch}.rpm']
    lines = []
    for path in artifacts:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        lines.append(f'{digest.hexdigest()}  {path.name}')
    (output / f'{stem}.sha256').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def rpm_dependencies(runtime):
    """Require external ELF capabilities, removing capabilities supplied inside the private runtime."""
    files = []
    for path in runtime.rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                if stream.read(4) == b'\x7fELF':
                    files.append(str(path))
    scanner = shutil.which('rpmdeps') or '/usr/lib/rpm/rpmdeps'
    # SONAME/symbol requirements work across RPM distributions without guessing package names.
    needed = subprocess.check_output([scanner, '--requires', *files], text=True).splitlines()
    supplied = subprocess.check_output([scanner, '--provides', *files], text=True).splitlines()
    # NumPy's repaired wheels can retain original symbol-version namespaces after
    # hashing a library's SONAME. Match its unversioned private SONAME as well,
    # so no impossible dependency on a wheel-private hashed library escapes.
    private_names = {value.split('(', 1)[0] for value in supplied if '()' in value}
    return sorted(value for value in set(needed) - set(supplied)
                  if value.split('(', 1)[0] not in private_names)


def main():
    """Create and check distributions without freezing or copying third-party libraries."""
    os.umask(0o022)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=PROJECT / 'dist/linux')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Linux packages must be built on Linux')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    host = PROJECT / 'build-linux-release/bin/Release/vst3-host-service'
    if not host.is_file():
        parser.error('Build the Release native host before packaging')
    result = subprocess.run(['ldd', str(host)], text=True, capture_output=True, check=True)
    (WORK / 'dependency-audit.json').write_text(json.dumps({'host_ldd': result.stdout}, indent=2) + '\n')
    if 'not found' in result.stdout:
        raise RuntimeError('Native host has unresolved shared libraries: ' + result.stdout)
    package(metadata.version(NAME), host, args.output)
    print(f'Release files: {args.output}')


if __name__ == '__main__':
    main()
