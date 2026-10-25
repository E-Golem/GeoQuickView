"""GeoQuickView: offline geospatial quick-look viewer."""
import os
import sys
from pathlib import Path

__version__ = "0.1.0"
_dll_handles = []
if os.name == "nt" and hasattr(os, "add_dll_directory"):
    for _directory in (Path(sys.prefix) / "Library" / "bin", Path(getattr(sys, "_MEIPASS", sys.prefix))):
        if _directory.is_dir():
            _dll_handles.append(os.add_dll_directory(str(_directory)))
