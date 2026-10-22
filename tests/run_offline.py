"""Run the complete unittest suite offline, without inherited credentials.

Usage: python tests/run_offline.py
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from tests.offline_guard import install
    install()
    # No API keys, provider settings, proxy variables, or user config directory
    # survive. Test-specific dummy credentials must be supplied by explicit mocks.
    os.environ.clear()
    with tempfile.TemporaryDirectory(prefix='offline-test-home-') as home:
        os.environ.update({'HOME': home, 'TMPDIR': home, 'TZ': 'UTC',
                           'PAPA_WHATSAPP_ENABLED': 'false',
                           'PYTHONDONTWRITEBYTECODE': '1'})
        os.chdir(root)
        print('OFFLINE: audit guard active; inherited environment cleared; temporary HOME', flush=True)
        suite = unittest.defaultTestLoader.discover(str(root), top_level_dir=str(root))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
