"""Verify the built portable zip the same way CI does, with local temp handling.

`scripts/verify_bundle.py` extracts the archive into a temporary directory and
runs the packaged exe with Conda/Python/GDAL/Qt search paths removed. Two local
constraints need to be honoured here:

* Some sandboxes create temp directories that cannot be written into, so the
  extraction root is created as a plain directory inside the project.
* PROJ refuses data directories whose path is non-ASCII. The project lives on a
  Chinese path, so the packaged app mirrors PROJ data to the OS temp directory;
  that mirror must stay reachable, which means TEMP must be an ASCII location.

Usage:
    python scripts\\verify_portable.py
"""
from pathlib import Path
import os
import runpy
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if not any((ROOT / "dist").glob("GeoQuickView-*-windows-x64.zip")):
        raise SystemExit("Build the portable zip first: python scripts\\build_windows.py")

    scratch = ROOT / "artifacts" / "portable-verify"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)

    counter = [0]

    def mkdtemp(suffix=None, prefix=None, dir=None):
        counter[0] += 1
        path = scratch / f"t{counter[0]}"
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

    # The packaged exe needs an ASCII TEMP for its PROJ mirror and its scratch
    # files. Prefer the OS temp directory (the normal case) and fall back to
    # LOCALAPPDATA\Temp only when it is itself an ASCII path.
    exe_temp = Path(tempfile.gettempdir())
    if not str(exe_temp).isascii():
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp"
        if candidate.is_dir() and str(candidate).isascii():
            exe_temp = candidate
    if not str(exe_temp).isascii():
        raise SystemExit(
            f"TEMP must be an ASCII path for the packaged app, got: {exe_temp}\n"
            "Run this from a normal terminal, or set TEMP to an ASCII directory."
        )
    for name in ("TEMP", "TMP"):
        os.environ[name] = str(exe_temp)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    runpy.run_path(str(ROOT / "scripts" / "verify_bundle.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())