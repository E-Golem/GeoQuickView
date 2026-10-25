"""Measure metadata/preview separately; sparse cases verify allocation, not real I/O speed."""
from pathlib import Path
import argparse
import ctypes
import json
import platform
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
started = time.perf_counter()
import geoquickview
import numpy as np
from osgeo import gdal
from geoquickview.data import inspect, render

class Counters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

def memory():
    result = Counters()
    result.cb = ctypes.sizeof(result)
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not api(handle, ctypes.byref(result), result.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"working_set": result.WorkingSetSize, "peak_working_set": result.PeakWorkingSetSize}

def measure(path, tag):
    before_files = {str(p): (p.stat().st_size, p.stat().st_mtime_ns)
                    for p in Path(path).parent.glob(Path(path).name+"*") if p.is_file()}
    before = memory()
    start = time.perf_counter()
    layer = inspect(path)
    metadata_seconds = time.perf_counter()-start
    start = time.perf_counter()
    frame = render(layer, target=layer.wkt, edge=1536)
    preview_seconds = time.perf_counter()-start
    after_files = {str(p): (p.stat().st_size, p.stat().st_mtime_ns)
                   for p in Path(path).parent.glob(Path(path).name+"*") if p.is_file()}
    assert before_files == after_files, "Source or sidecar changed"
    return {"case": tag, "disk_bytes": Path(path).stat().st_size, "bands": len(layer.choices),
            "dimensions": layer.metadata["宽 × 高"], "metadata_seconds": metadata_seconds,
            "preview_seconds": preview_seconds, "preview_shape": list(frame.data.shape),
            "selected_bands": layer.selection["bands"], "memory_before": before,
            "memory_after": memory(), "source_and_sidecars_unchanged": True}

parser = argparse.ArgumentParser()
parser.add_argument("raster", nargs="?")
parser.add_argument("--sparse-case", action="store_true")
arguments = parser.parse_args()
report = {"platform": platform.platform(), "python": sys.version, "gdal": gdal.VersionInfo(),
          "import_seconds": time.perf_counter()-started, "cases": []}
if arguments.raster:
    report["cases"].append(measure(arguments.raster, "real raster; timings depend on OS cache and existing overviews"))
if arguments.sparse_case:
    with tempfile.TemporaryDirectory(prefix="geoquickview-benchmark-") as directory:
        path = Path(directory)/"logical_12GiB.tif"
        ds = gdal.GetDriverByName("GTiff").Create(str(path), 16384, 16384, 12, gdal.GDT_Float32,
                                                 ["TILED=YES", "SPARSE_OK=YES", "BIGTIFF=YES"])
        ds.SetGeoTransform((70, .005, 0, 55, 0, -.005))
        from geoquickview.data import reference
        ds.SetProjection(reference("EPSG:4326").ExportToWkt())
        ds = None
        result = measure(path, "sparse synthetic; allocation check only, not representative disk throughput")
        result["logical_bytes"] = 16384*16384*12*4
        assert max(result["preview_shape"]) <= 1536
        report["cases"].append(result)
output = ROOT/"artifacts"/"benchmark.json"
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
