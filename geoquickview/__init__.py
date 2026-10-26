"""GeoQuickView: offline geospatial quick-look viewer."""
import os
import sys
from pathlib import Path

__version__ = "0.2.0"
_dll_handles = []
_runtime = Path(getattr(sys, "_MEIPASS", sys.prefix))
_library = _runtime / "Library"
if os.name == "nt" and hasattr(os, "add_dll_directory"):
    _binary = _library / "bin" if (_library / "bin").is_dir() else _runtime
    os.environ["PATH"] = str(_binary) + os.pathsep + os.environ.get("PATH", "")
    _dll_handles.append(os.add_dll_directory(str(_binary)))
for _key, _candidate in {
    "PROJ_DATA": _library / "share" / "proj",
    "PROJ_LIB": _library / "share" / "proj",
    "GDAL_DATA": _library / "share" / "gdal",
    "GDAL_DRIVER_PATH": _library / "lib" / "gdalplugins",
}.items():
    if _candidate.is_dir():
        os.environ[_key] = str(_candidate)

import tempfile
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "GeoQuickView" / "matplotlib"))
