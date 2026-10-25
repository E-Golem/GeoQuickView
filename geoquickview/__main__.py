"""Launch the native app, or run an offline diagnostic smoke test."""
from pathlib import Path
import argparse
import json
import sys
import tempfile

from . import __version__


def self_check(destination):
    import numpy as np
    from osgeo import gdal
    from .samples import create_samples
    from .data import inspect, render, reproject, reference, query, exact_statistics
    from .export import export_map
    directory = Path(destination).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    files = create_samples(directory/"samples")
    results = {}
    layers = {}
    for kind in ("raster", "nc", "vector", "gpkg", "shp"):
        layer = inspect(files[kind])
        frame = render(layer, target=layer.wkt, edge=192)
        results[kind] = {"kind": layer.kind, "shape": list(frame.data.shape) if frame.data is not None else None,
                         "geometries": len(frame.geometries), "note": frame.note}
        layers[kind] = layer
    raster = layers["raster"]
    reproject(raster, {"bands": [1]}, directory/"reprojected.tif", "EPSG:3857")
    export_map([(raster, raster.selection, {"lower": 278, "upper": 330}),
                (layers["vector"], layers["vector"].selection, {"line": "#be123c"})],
               raster.extent, raster.wkt, directory/"map.png", dpi=120, title="合成研究区 · GeoQuickView")
    export_map([(raster, raster.selection, {"lower": 278, "upper": 330})],
               raster.extent, raster.wkt, directory/"map.pdf", dpi=120, title="合成研究区 · GeoQuickView")
    nc_selection = {"variable": "temperature", "indices": {"time": 1, "level": 1}}
    nc_value = query(layers["nc"], nc_selection, -175, -5, reference("EPSG:4326").ExportToWkt())
    if abs(nc_value["物理值"]-312.29) > 1e-8:
        raise AssertionError("NetCDF slicing or scaling mismatch")
    reproject(layers["nc"], nc_selection, directory/"nc-reprojected.tif", "EPSG:4326")
    reproject(layers["vector"], layers["vector"].selection, directory/"vector-reprojected.gpkg", "EPSG:3857")
    results["nc_pixel"] = nc_value
    results["statistics"] = exact_statistics(raster, {"bands": [1]})
    results["gdal"] = gdal.VersionInfo()
    results["version"] = __version__
    results["drivers"] = {name: gdal.GetDriverByName(name) is not None for name in ("GTiff", "netCDF", "HDF5", "GPKG", "ESRI Shapefile")}
    (directory/"self-check.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def main():
    parser = argparse.ArgumentParser(description="GeoQuickView")
    parser.add_argument("files", nargs="*")
    parser.add_argument("--self-check", metavar="OUTPUT_DIRECTORY")
    parser.add_argument("--smoke-test", metavar="SCREENSHOT")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    if args.self_check:
        self_check(args.self_check)
        return 0
    from PySide6 import QtCore, QtWidgets, QtGui
    from .app import MainWindow
    app = QtWidgets.QApplication(sys.argv[:1])
    import os
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        if (fonts / filename).is_file():
            QtGui.QFontDatabase.addApplicationFont(str(fonts / filename))
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 10))
    app.setApplicationName("GeoQuickView")
    app.setOrganizationName("E-Golem")
    window = MainWindow()
    icon = QtGui.QIcon(str(Path(__file__).with_name("assets") / "icon.svg"))
    app.setWindowIcon(icon)
    window.setWindowIcon(icon)
    errors = []
    if args.smoke_test:
        window.show_error = lambda message: errors.append(message)
    window.show()
    if args.files:
        for path in args.files:
            window.open_path(path)
    elif args.smoke_test:
        window.open_demo()
    if args.smoke_test:
        elapsed = QtCore.QElapsedTimer()
        elapsed.start()
        timer = QtCore.QTimer()
        timer.setInterval(200)
        def check():
            if (window.entries and not window.jobs and not window.rendering and not window.render_timer.isActive()) or elapsed.elapsed() > 45000:
                output = Path(args.smoke_test).resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(output))
                report = {"errors": errors, "layers": len(window.entries),
                          "rendered": sum(entry["frame"] is not None for entry in window.entries.values()),
                          "metadata_only": sum(entry["layer"].kind == "nc" and not entry["selection"].get("variable")
                                               for entry in window.entries.values()),
                          "attributes_displayed": window.properties.topLevelItemCount() > 0,
                          "selections": [entry["selection"] for entry in window.entries.values()],
                          "elapsed_ms": elapsed.elapsed()}
                output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                timer.stop()
                window.close()
                metadata_valid = report["layers"] > 0 and report["metadata_only"] == report["layers"] and report["attributes_displayed"]
                app.exit(1 if errors or not (report["rendered"] or metadata_valid) else 0)
        timer.timeout.connect(check)
        timer.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
