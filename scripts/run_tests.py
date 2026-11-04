"""Run the test suite with a writable temporary directory.

The default unittest command keeps temporary files in the system temp folder.
Some sandboxes create that folder but refuse to write inside it, and the tests
build their synthetic rasters and NetCDF files there. Pointing tempfile at a
folder inside the project keeps the tests runnable in both environments.

Usage:
    python scripts\\run_tests.py            # integration tests
    python scripts\\run_tests.py --gui      # integration tests + Qt checks
"""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def redirect_temporary_directory():
    """Make tempfile hand out directories under artifacts\\ that are writable."""
    if not (ROOT / "artifacts").is_dir():
        (ROOT / "artifacts").mkdir(parents=True, exist_ok=True)
    base = ROOT / "artifacts" / "test-tmp"
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    counter = [0]

    def mkdtemp(suffix=None, prefix=None, dir=None):
        counter[0] += 1
        path = base / f"t{counter[0]}{suffix or ''}"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    class TemporaryDirectory:
        def __init__(self, suffix=None, prefix=None, dir=None):
            self.name = mkdtemp(suffix)

        def __enter__(self):
            return self.name

        def __exit__(self, *exception):
            return False

        def cleanup(self):
            return None

    tempfile.mkdtemp = mkdtemp
    tempfile.TemporaryDirectory = TemporaryDirectory
    return base


def main():
    base = redirect_temporary_directory()
    suite = unittest.TestLoader().discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"\ninterim files: {base}")
    if "--gui" in sys.argv[1:]:
        import runpy
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        runpy.run_module("tests.gui_smoke", run_name="__main__")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())