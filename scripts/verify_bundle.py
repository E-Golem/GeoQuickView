"""Verify the extracted exe with Conda/Python/GDAL/Qt search paths removed."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import zipfile
import sys

ROOT = Path(__file__).resolve().parents[1]
archives = sorted((ROOT/"dist").glob("GeoQuickView-*-windows-x64.zip"), key=lambda p: p.stat().st_mtime)
if not archives:
    raise SystemExit("Build the portable zip first.")
artifact = ROOT/"artifacts"
artifact.mkdir(exist_ok=True)
environment = os.environ.copy()
for name in ("PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX", "GDAL_DRIVER_PATH", "GDAL_DATA", "PROJ_DATA", "PROJ_LIB",
             "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYQTGRAPH_QT_LIB"):
    environment.pop(name, None)
windows = Path(environment.get("SystemRoot", "C:/Windows"))
environment["PATH"] = str(windows/"System32")+os.pathsep+str(windows)
environment["QT_QPA_PLATFORM"] = "offscreen"
environment["PROJ_NETWORK"] = "OFF"
report = {"archive": archives[-1].name, "path_isolation": True, "checks": []}
with tempfile.TemporaryDirectory(prefix="GeoQuickView-portable-") as temporary:
    directory = Path(temporary)
    with zipfile.ZipFile(archives[-1]) as archive:
        for member in archive.infolist():
            if not (directory/member.filename).resolve().is_relative_to(directory.resolve()):
                raise RuntimeError("Invalid archive path.")
        archive.extractall(directory)
    executable = directory/"GeoQuickView"/"GeoQuickView.exe"
    output = directory/"独立运行验证"
    def run(arguments):
        result = subprocess.run([str(executable), *map(str, arguments)], env=environment, cwd=directory,
                                timeout=180, creationflags=subprocess.CREATE_NO_WINDOW,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            crash = Path(tempfile.gettempdir())/"GeoQuickView"/"crash.log"
            raise RuntimeError(f"Standalone exe failed ({result.returncode}):\n"+
                               (crash.read_text(encoding="utf-8") if crash.exists() else result.stderr.decode(errors="replace")))
    run(["--self-check", output])
    backend = json.loads((output/"self-check.json").read_text(encoding="utf-8"))
    assert all(backend["drivers"][name] for name in ("GTiff", "netCDF", "GPKG", "ESRI Shapefile")), backend
    assert (output/"map.png").stat().st_size > 5000
    assert (output/"map.pdf").read_bytes().startswith(b"%PDF")
    report["checks"].append("backend, projections, formats and PNG/PDF export")
    for name, files in (("raster-vector", [output/"samples"/"demo_monthly.tif", output/"samples"/"demo_boundary.geojson"]),
                        ("netcdf", [output/"samples"/"demo_weather.nc"])):
        screenshot = output/f"ui-{name}.png"
        run(["--smoke-test", screenshot, *files])
        ui = json.loads(screenshot.with_suffix(".json").read_text(encoding="utf-8"))
        assert not ui["errors"] and ui["rendered"] == len(files), ui
        report["checks"].append({name: ui})
        import shutil
        shutil.copy2(screenshot, artifact/f"portable-{name}.png")
    report["backend"] = backend
    report["passed"] = True
(artifact/"portable-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
