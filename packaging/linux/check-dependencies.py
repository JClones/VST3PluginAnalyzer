#!/usr/bin/env python3
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Validate an external Python runtime before installing the source tar distribution."""
import importlib
import re
import sys


def version_tuple(value):
    return tuple(int(part) for part in re.match(r'\d+(?:\.\d+)*', value).group().split('.'))


def main():
    errors = []
    if sys.version_info < (3, 10):
        errors.append('Python >= 3.10 is required')
    for name, minimum, maximum in (('numpy', (2, 0), (3,)),
                                   ('PySide6', (6, 8), (6, 12) if sys.version_info < (3, 12) else (7,)),
                                   ('pyqtgraph', (0, 13, 7), (1,))):
        try:
            module = importlib.import_module(name)
            version = version_tuple(module.__version__)
            if not minimum <= version < maximum:
                errors.append(f'{name} {module.__version__}: requires >= {".".join(map(str, minimum))}, < {".".join(map(str, maximum))}')
        except (ImportError, OSError) as error:
            errors.append(f'{name}: {error}')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        print('Install the dependencies described in README before installing the application.', file=sys.stderr)
        return 1
    print('Python dependencies are compatible.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
