#!/usr/bin/env python3
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Remove generated project files; preview by default and delete only with --apply."""
import argparse
from pathlib import Path
import shutil

PROJECT = Path(__file__).resolve().parents[1]  # Checkout containing this script.
BUILD_DIRECTORIES = {
    'build', 'build-local', 'build-linux-validation', 'build-linux-bundle', 'build-linux-install',
    'build-macos-bundle',
}  # Generated working directories that do not necessarily contain a CMake cache.
GENERATED_DIRECTORIES = {
    '.tools', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov', 'Testing',
}  # Downloaded build tools, test reports and local development caches.
SOURCE_DIRECTORIES = (
    'cpp', 'python', 'scripts', 'docs', 'packaging',
)  # Project-owned trees scanned for generated Python caches without entering the SDK.
CACHE_DIRECTORIES = {
    '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
}  # Generated directories removable within source trees.


def cleanup_candidates(project, keep_dist=False):
    """Find generated paths inside project; keep_dist preserves its release artifacts."""
    candidates = set()
    # Restrict whole-directory deletion to build conventions and identifiable CMake builds.
    for path in project.iterdir():
        name = path.name
        cmake_build = (name.startswith('build-') and not path.is_symlink()
                       and (path / 'CMakeCache.txt').is_file())
        environment = name == '.venv' or name.startswith('.venv-')
        if (name in BUILD_DIRECTORIES or cmake_build or environment
                or name in GENERATED_DIRECTORIES or (name == 'dist' and not keep_dist)):
            candidates.add(path)
        elif path.is_file() and (path.suffix in ('.log', '.pyc', '.pyo')
                                  or name in ('.coverage', 'coverage.xml', '.DS_Store')
                                  or name.startswith('.coverage.')):
            candidates.add(path)
    # Never follow symlinks, Git metadata or the SDK.
    pending = [project / name for name in SOURCE_DIRECTORIES]
    while pending:
        directory = pending.pop()
        if directory.is_symlink() or not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_symlink():
                continue
            if path.is_dir():
                if path.name in CACHE_DIRECTORIES or path.name.endswith('.egg-info'):
                    candidates.add(path)
                elif path.name != '.git':
                    pending.append(path)
            elif path.suffix in ('.pyc', '.pyo') or path.name == '.DS_Store':
                candidates.add(path)
    return sorted(candidates)


def remove_generated(path):
    """Remove generated path itself, unlinking symlinks without touching their targets."""
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)


def main():
    """Preview generated paths, or delete them when the caller explicitly passes --apply."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Delete the listed generated files and directories')
    parser.add_argument('--keep-dist', action='store_true', help='Preserve built application bundles and release archives')
    options = parser.parse_args()
    candidates = cleanup_candidates(PROJECT, options.keep_dist)
    # Resolve the project from the script location rather than the caller's working directory.
    print(f'Project: {PROJECT}')
    print('Removing:' if options.apply else 'Preview only; use --apply to delete:')
    for path in candidates:
        print(f'  {path.relative_to(PROJECT)}')
        if options.apply:
            remove_generated(path)
    print(f'{len(candidates)} generated paths {"removed" if options.apply else "identified"}.')
    print('Sources, SDK, Git metadata, application configuration are preserved.')


if __name__ == '__main__':
    main()
