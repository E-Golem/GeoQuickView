# -*- mode: python ; coding: utf-8 -*-
"""Build an onedir Windows bundle including offline projection data and GDAL plugins."""
import os
import sys
from pathlib import Path

root = Path(SPECPATH)
prefix = Path(sys.prefix)
os.environ["PATH"] = str(prefix/"Library"/"bin") + os.pathsep + os.environ.get("PATH", "")
from PyInstaller.utils.hooks import collect_data_files
datas = collect_data_files("geoquickview", includes=["assets/*"])
datas += [(str(prefix/"Library"/"share"/"gdal"), "Library/share/gdal"),
          (str(prefix/"Library"/"share"/"proj"), "Library/share/proj")]
binaries = [(str(path), "Library/lib/gdalplugins") for path in (prefix/"Library"/"lib"/"gdalplugins").glob("*.dll")]
a = Analysis([str(root/"scripts"/"launch.py")], pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=["xarray.backends.h5netcdf_", "xarray.backends.scipy_", "h5netcdf", "h5py", "cftime", "osgeo.gdal_array", "matplotlib.backends.backend_pdf"],
             excludes=["PyQt5", "PyQt6", "PySide2", "IPython", "pytest", "tkinter"],
             hooksconfig={"matplotlib": {"backends": ["Agg", "pdf"]}}, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GeoQuickView", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(root/"artifacts"/"icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GeoQuickView")
