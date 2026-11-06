"""GeoQuickView: offline geospatial quick-look viewer."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

__version__ = "0.3.1"

_PROJ_KEYS = ("PROJ_DATA", "PROJ_LIB")


def _is_ascii(text):
    try:
        str(text).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _copy_tree(source, target):
    """Copy `source` into `target` file by file.

    shutil.copytree is avoided on purpose: it recreates nested directories in
    one pass, and some managed environments only allow single-level creation.
    """
    target.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        destination = target / entry.name
        if entry.is_dir():
            _copy_tree(entry, destination)
        elif entry.is_file():
            shutil.copyfile(entry, destination)


def _ascii_mirror(source):
    """Return a PROJ-readable path holding a copy of `source`.

    PROJ cannot open proj.db when the path to it contains non-ASCII characters,
    which breaks every coordinate system lookup. When the install or extracted
    location has such a path, copy the data once to an ASCII cache directory and
    point PROJ there. Returns None when no ASCII location is writable.
    """
    expected = (source / "proj.db").stat().st_size
    candidates = []
    # The OS temp directory is the safest place for a machine-local cache:
    # tempfile.gettempdir() already honours TEMP/TMP, and the explicit entries
    # below keep the lookup working when TEMP points somewhere non-ASCII.
    candidates.append(Path(tempfile.gettempdir()) / "GeoQuickView" / "proj-data")
    for name in ("TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value) / "GeoQuickView" / "proj-data")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "GeoQuickView" / "proj-data")
    candidates.append(Path.home() / ".geoquickview" / "proj-data")
    # Divergent in-box entries for TEMP/TMP should still fall back to the
    # system-wide temp root rather than giving up entirely.
    system_temp = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if system_temp:
        candidates.append(Path(system_temp) / "Temp" / "GeoQuickView" / "proj-data")

    for cached in candidates:
        if not _is_ascii(cached):
            continue
        marker = cached / "proj.db"
        try:
            if marker.is_file() and marker.stat().st_size == expected:
                return cached
            _copy_tree(source, cached)
            if marker.is_file() and marker.stat().st_size == expected:
                return cached
        except OSError:
            continue
    return None


# Keep the handles alive for the process lifetime; dropping them would close the
# DLL search directory and break the native imports that follow.
_dll_handles = []
_runtime = Path(getattr(sys, "_MEIPASS", sys.prefix))
_library = _runtime / "Library"
if os.name == "nt" and hasattr(os, "add_dll_directory"):
    _binary = _library / "bin" if (_library / "bin").is_dir() else _runtime
    os.environ["PATH"] = str(_binary) + os.pathsep + os.environ.get("PATH", "")
    _dll_handles.append(os.add_dll_directory(str(_binary)))

# An inherited PROJ_DATA/PROJ_LIB may point at unrelated or removed software;
# never trust the ambient value for a self-contained app.
for _key in _PROJ_KEYS:
    os.environ.pop(_key, None)

_proj = _library / "share" / "proj"
if _proj.is_dir():
    _target = _proj if _is_ascii(_proj) else _ascii_mirror(_proj)
    if _target is not None:
        os.environ["PROJ_DATA"] = str(_target)
        os.environ["PROJ_LIB"] = str(_target)
for _key, _candidate in {
    "GDAL_DATA": _library / "share" / "gdal",
    "GDAL_DRIVER_PATH": _library / "lib" / "gdalplugins",
}.items():
    if _candidate.is_dir():
        os.environ[_key] = str(_candidate)
    else:
        os.environ.pop(_key, None)

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "GeoQuickView" / "matplotlib"))
