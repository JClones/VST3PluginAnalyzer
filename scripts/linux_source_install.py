#!/usr/bin/env python3
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Prepare a source-built Linux runtime without root, then install or remove it."""
import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]  # Source checkout when preparing; unused by installed removal.
NAME = 'vst3-plugin-analyzer'  # Shared launcher, runtime and desktop identity.
MARKER = '.analyzer-source-install.json'  # Distinguishes source installs from tar/system packages.


def run(*command):
    """Execute a build/install subprocess, propagating failure instead of hiding it."""
    subprocess.run([str(part) for part in command], check=True)


def prepare(args):
    """Build wheels and a standalone venv payload using the caller's tested environment."""
    if os.geteuid() == 0:
        raise RuntimeError('Prepare without sudo; only the final install needs root.')
    host = (PROJECT / args.build_directory / 'bin/Release/vst3-host-service').resolve()
    if not host.is_file() or sys.prefix == sys.base_prefix:
        raise RuntimeError('Run ./scripts/build-linux.sh first, then ./scripts/prepare-linux-install.sh.')
    run(sys.executable, '-m', 'pip', 'check')
    output = args.prepared.resolve()
    output.mkdir(parents=True, exist_ok=True)
    wheels = output / 'wheels'
    wheels.mkdir(exist_ok=True)
    # Keep the tested dependency versions; exclude editable project metadata from constraints.
    constraints = output / 'constraints.txt'
    constraints.write_text(''.join(f'{dist.metadata["Name"]}=={dist.version}\n'
                                   for dist in metadata.distributions()
                                   if dist.metadata['Name'].lower().replace('_', '-') != NAME))
    run(sys.executable, '-m', 'pip', 'wheel', '--only-binary=:all:', '--wheel-dir', wheels,
        '--constraint', constraints, str(PROJECT) + '[gui]')
    version = metadata.version(NAME)
    # A fresh payload removes stale modules on rebuild. Its venv uses copies of system Python,
    # not links to the source checkout; launching through bin/python survives relocation.
    with tempfile.TemporaryDirectory(prefix='payload-', dir=output) as temporary:
        runtime = Path(temporary) / 'runtime'
        run(sys.executable, '-m', 'venv', '--copies', '--without-pip', runtime / 'venv')
        python = runtime / 'venv/bin/python'
        site = subprocess.check_output([str(python), '-c',
                                       'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
        run(sys.executable, '-m', 'pip', 'install', '--no-index', '--find-links', wheels,
            '--no-compile', '--target', site, f'{NAME}[gui]=={version}')
        (runtime / 'native').mkdir()
        shutil.copy2(host, runtime / 'native/vst3-host-service')
        shutil.copy2(Path(__file__), runtime / 'linux_source_install.py')
        notices = runtime / 'licenses'
        notices.mkdir()
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
            shutil.copy2(PROJECT / name, notices / name)
        shutil.copy2(PROJECT / 'vst3sdk/LICENSE.txt', notices / 'VST3-SDK-LICENSE.txt')
        for component in ('base', 'pluginterfaces', 'public.sdk'):
            shutil.copy2(PROJECT / 'vst3sdk' / component / 'LICENSE.txt', notices / (component + '-LICENSE.txt'))
        shutil.copy2(PROJECT / 'vst3sdk/vstgui4/vstgui/thirdparty/rapidjson/license.txt',
                     notices / 'RapidJSON-LICENSE.txt')
        launcher = runtime / NAME
        launcher.symlink_to('native/vst3-host-service')
        shutil.copy2(PROJECT / 'packaging/common/entrypoint.py', runtime / 'entrypoint.py')
        shutil.copy2(PROJECT / 'packaging/linux/vst3-plugin-analyzer.desktop', Path(temporary) / (NAME + '.desktop'))
        shutil.copy2(PROJECT / 'python/vst3_analyzer/ui/assets/VST3_Icon.svg', Path(temporary) / (NAME + '.svg'))
        (runtime / MARKER).write_text(json.dumps({'version': version, 'format': 1}) + '\n')
        # Exercise the prepared Python packages and host before any privileged filesystem writes.
        run(launcher, '--help')
        run(python, '-I', '-c', 'import numpy, PySide6.QtWidgets, pyqtgraph, vst3_analyzer.ui')
        run(python, '-I', '-c',
            'import sys\nfrom vst3_analyzer.ipc.client import HostClient\n'
            'with HostClient(sys.argv[1], initialization_timeout=30) as client: print(client.capabilities)',
            runtime / 'native/vst3-host-service')
        payload = output / 'payload'
        if payload.exists():
            shutil.rmtree(payload)
        shutil.move(temporary, payload)
    print(f'Prepared {output / "payload"}. Install: sudo ./scripts/install-linux-source.sh')


def locations(args):
    """Return staging root and installation prefix, refusing paths that escape that root."""
    prefix = Path(args.prefix)
    if not prefix.is_absolute() or '..' in prefix.parts or prefix == Path('/'):
        raise RuntimeError('Use an absolute non-root prefix such as /usr.')
    root = args.destdir.resolve() if args.destdir else Path('/')
    base = root / str(prefix).lstrip('/')
    # Do not follow symlinks when checking or replacing installation-owned files.
    for relative in ('lib/' + NAME, 'bin/' + NAME, 'share/applications/' + NAME + '.desktop',
                     'share/icons/hicolor/scalable/apps/' + NAME + '.svg'):
        path = base / relative
        for ancestor in (path, *path.parents):
            if ancestor == root:
                break
            if ancestor.is_symlink():
                # Our own launcher link is validated separately below.
                if ancestor != base / 'bin' / NAME:
                    raise RuntimeError(f'Refusing installation through a symlink: {ancestor}')
    return base, prefix


def check_owned(base, prefix):
    """Reject unrelated runtime/desktop files and installations made by other helpers."""
    runtime = base / 'lib' / NAME
    marker = runtime / MARKER
    if runtime.exists():
        if not marker.is_file() or json.loads(marker.read_text()).get('prefix') != str(prefix):
            raise RuntimeError(f'{runtime} is not a marked source installation; remove it with its installer.')
    launcher = base / 'bin' / NAME
    if launcher.exists() or launcher.is_symlink():
        if not launcher.is_symlink() or os.readlink(launcher) != '../lib/' + NAME + '/' + NAME:
            raise RuntimeError(f'Refusing to replace unrelated launcher: {launcher}')
    for file in (base / 'share/applications' / (NAME + '.desktop'),
                 base / 'share/icons/hicolor/scalable/apps' / (NAME + '.svg')):
        if file.exists() and not marker.is_file():
            raise RuntimeError(f'Refusing to replace unrelated desktop asset: {file}')
    return runtime


def refresh_desktop(base, staged):
    """Refresh available desktop/icon caches only for a real installation."""
    if staged:
        return
    for command in (('update-desktop-database', str(base / 'share/applications')),
                    ('gtk-update-icon-cache', '-q', '-t', str(base / 'share/icons/hicolor'))):
        if shutil.which(command[0]):
            subprocess.run(command, check=False)


def install(args):
    """Copy a previously prepared runtime and desktop assets, with no network or pip under sudo."""
    base, prefix = locations(args)
    runtime = check_owned(base, prefix)
    payload = args.prepared.resolve() / 'payload'
    source = payload / 'runtime'
    if not (source / MARKER).is_file() or not (source / 'venv/bin/python').is_file():
        raise RuntimeError('Missing prepared runtime. Run ./scripts/prepare-linux-install.sh without sudo first.')
    # Prevent a second system launcher/menu entry shadowing an earlier tar or package install.
    if not args.destdir and prefix in (Path('/usr'), Path('/usr/local')):
        other = Path('/usr/local' if str(prefix) == '/usr' else '/usr') / 'lib' / NAME
        if other.exists():
            raise RuntimeError(f'Remove the existing installation at {other} with its installer first.')
    runtime.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=NAME + '.new-', dir=runtime.parent) as temporary:
        replacement = Path(temporary) / NAME
        shutil.copytree(source, replacement, symlinks=True)
        # A developer's group-writable umask must not leak into the system runtime.
        for path in (replacement, *replacement.rglob('*')):
            if not path.is_symlink():
                path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
        marker = json.loads((replacement / MARKER).read_text())
        marker['prefix'] = str(prefix)
        (replacement / MARKER).write_text(json.dumps(marker) + '\n')
        if runtime.exists():
            runtime.rename(Path(temporary) / 'previous')
        try:
            replacement.rename(runtime)
        except OSError:
            previous = Path(temporary) / 'previous'
            if previous.exists():
                previous.rename(runtime)
            raise
    launcher = base / 'bin' / NAME
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.unlink(missing_ok=True)
    launcher.symlink_to('../lib/' + NAME + '/' + NAME)
    desktop = base / 'share/applications' / (NAME + '.desktop')
    desktop.parent.mkdir(parents=True, exist_ok=True)
    # Absolute Exec keeps application-menu launches independent of the session PATH.
    text = (payload / (NAME + '.desktop')).read_text()
    executable = str(prefix / 'bin' / NAME).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    text = text.replace('Exec=' + NAME, f'Exec="{executable}"')
    desktop.write_text(text)
    desktop.chmod(0o644)
    icon = base / 'share/icons/hicolor/scalable/apps' / (NAME + '.svg')
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(payload / (NAME + '.svg'), icon)
    icon.chmod(0o644)
    refresh_desktop(base, args.destdir)
    print(f'Installed {runtime}; desktop entry: {desktop}')
    print(f'Uninstall: sudo python3 {prefix / "lib" / NAME / "linux_source_install.py"} uninstall --prefix {prefix}')


def uninstall(args):
    """Remove only a marked source runtime and its owned launcher/desktop assets."""
    base, prefix = locations(args)
    runtime = check_owned(base, prefix)
    if not (runtime / MARKER).is_file():
        raise RuntimeError('No marked source installation found.')
    for file in (base / 'bin' / NAME, base / 'share/applications' / (NAME + '.desktop'),
                 base / 'share/icons/hicolor/scalable/apps' / (NAME + '.svg')):
        file.unlink(missing_ok=True)
    shutil.rmtree(runtime)
    refresh_desktop(base, args.destdir)
    print('Source installation removed. User configuration and plugins were preserved.')


def main():
    """Parse preparation, installation or removal arguments and report actionable errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'install', 'uninstall'))
    parser.add_argument('--prepared', type=Path, default=PROJECT / 'build-linux-install')
    parser.add_argument('--build-directory', type=Path, default=Path('build'))
    parser.add_argument('--prefix', default='/usr')
    parser.add_argument('--destdir', type=Path, help='Stage installation under this directory without root')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('This installer is for Linux.')
    try:
        {'prepare': prepare, 'install': install, 'uninstall': uninstall}[args.action](args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Source {args.action} failed: {error}\n')


if __name__ == '__main__':
    main()
