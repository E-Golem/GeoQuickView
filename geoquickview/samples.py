"""Small synthetic, explicitly labelled demo data; no external downloads."""
from pathlib import Path
import json
import numpy as np
from osgeo import gdal, ogr
from .data import reference


def create_samples(directory):
    import h5netcdf
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tif = directory/"demo_monthly.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(tif), 240, 160, 3, gdal.GDT_Int16,
                                             ["TILED=YES", "COMPRESS=LZW"])
    ds.SetProjection(reference("EPSG:4326").ExportToWkt())
    ds.SetGeoTransform((70, .25, 0, 55, 0, -.25))
    rows, cols = np.mgrid[:160, :240]
    for i in range(1, 4):
        values = (250+cols+rows*2+i*30).astype(np.int16)
        values[:5, :5] = -9999
        band = ds.GetRasterBand(i)
        band.WriteArray(values)
        band.SetNoDataValue(-9999)
        band.SetScale(.1)
        band.SetOffset(250)
        band.SetUnitType("K")
        band.SetDescription(f"合成演示 {i:02d} 月")
    ds = None
    boundary = directory/"demo_boundary.geojson"
    boundary.write_text(json.dumps({"type": "FeatureCollection", "name": "合成研究区",
        "features": [{"type": "Feature", "properties": {"name": "合成研究区", "value": 1},
                      "geometry": {"type": "Polygon", "coordinates": [[[82, 22], [120, 22], [120, 48], [82, 48], [82, 22]],
                                                                    [[95, 30], [95, 38], [103, 38], [103, 30], [95, 30]]]}}]},
                                  ensure_ascii=False), encoding="utf-8")
    for filename, driver in (("demo_boundary.gpkg", "GPKG"), ("demo_boundary.shp", "ESRI Shapefile")):
        gdal.VectorTranslate(str(directory/filename), str(boundary), format=driver, layerCreationOptions=["ENCODING=UTF-8"] if driver == "ESRI Shapefile" else [])
    nc = directory/"demo_weather.nc"
    with h5netcdf.File(nc, "w") as ds:
        ds.attrs.update(title="Synthetic demonstration data - not observations", Conventions="CF-1.8")
        ds.dimensions = {"time": 3, "level": 2, "lat": 25, "lon": 72}
        for name, values, units, axis in (
            ("time", [0, 24, 48], "hours since 2026-01-01 00:00:00", "T"),
            ("level", [1000, 850], "hPa", "Z"),
            ("lat", np.arange(-60, 65, 5), "degrees_north", "Y"),
            ("lon", np.arange(0, 360, 5), "degrees_east", "X")):
            var = ds.create_variable(name, (name,), dtype="f8", data=values)
            var.attrs.update(units=units, axis=axis)
            if name == "time":
                var.attrs["calendar"] = "360_day"
        raw = np.arange(3*2*25*72, dtype=np.int16).reshape(3, 2, 25, 72)
        raw[0, 0, 0, 0] = -9999
        var = ds.create_variable("temperature", ("time", "level", "lat", "lon"), dtype="i2",
                                 fillvalue=-9999, compression="gzip", data=raw)
        var.attrs.update(units="K", long_name="Synthetic air temperature", scale_factor=.01, add_offset=250.)
        rain = ds.create_variable("precipitation", ("time", "lat", "lon"), dtype="f4", fillvalue=-9999,
                                  data=np.abs(np.sin(np.arange(3*25*72).reshape(3, 25, 72)/30))*12)
        rain.attrs["units"] = "mm"
    return {"raster": str(tif), "nc": str(nc), "vector": str(boundary),
            "gpkg": str(directory/"demo_boundary.gpkg"), "shp": str(directory/"demo_boundary.shp")}
