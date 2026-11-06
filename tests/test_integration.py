"""Integration checks for geographic correctness and bounded reads, using synthetic data."""
import json
import tempfile
from pathlib import Path
import unittest
import threading
from unittest.mock import patch

import geoquickview
import numpy as np
from osgeo import gdal, ogr
from geoquickview.data import inspect, render, query, reference, reproject, exact_statistics, Cancelled
from geoquickview.nc import grid_info, dimension_label
from geoquickview.samples import create_samples
from geoquickview.export import export_map


class GeoIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.files = create_samples(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_raster_metadata_and_scaled_query(self):
        layer = inspect(self.files["raster"])
        self.assertEqual(layer.metadata["宽 × 高"], [240, 160])
        self.assertEqual(len(layer.choices), 3)
        value = query(layer, {"bands": [2]}, 72.625, 52.375, layer.wkt)["波段 2"]
        self.assertEqual(value["原始值"], 340)
        self.assertAlmostEqual(value["物理值"], 284.)
        self.assertFalse(query(layer, {"bands": [1]}, 70.1, 54.9, layer.wkt)["波段 1"]["有效"])

    def test_preview_bound_mask_rgb_and_viewport(self):
        layer = inspect(self.files["raster"])
        frame = render(layer, target=layer.wkt, edge=80)
        self.assertLessEqual(max(frame.data.shape), 80)
        self.assertTrue(np.isnan(frame.data[0, 0]))
        rgb = render(layer, {"bands": [1, 2, 3]}, layer.wkt, (72, 50, 74, 52), 32)
        self.assertEqual(rgb.data.shape, (32, 32, 3))
        self.assertTrue(275 < np.nanmin(rgb.data) < 300)

    def test_nc_coordinates_slice_and_missing_value(self):
        layer = inspect(self.files["nc"])
        grid = grid_info(layer.path, "temperature")
        self.assertEqual(grid["extra"], {"time": 3, "level": 2})
        self.assertEqual(float(grid["x"][0]), -180)
        self.assertEqual(float(grid["y"][0]), 60)
        selection = {"variable": "temperature", "indices": {"time": 1, "level": 1}}
        value = query(layer, selection, -175, -5, reference("EPSG:4326").ExportToWkt())
        self.assertEqual(value["原始列"], 37)
        self.assertAlmostEqual(value["物理值"], (3*1800+11*72+37)*.01+250)
        missing = query(layer, layer.selection, 0, -60, layer.wkt)
        self.assertIsNone(missing["物理值"])
        self.assertIn("2026-01-02", dimension_label(layer.path, "temperature", "time", 1))
        frame = render(layer, selection, layer.wkt, edge=64)
        self.assertLessEqual(max(frame.data.shape), 64)

    def test_vector_formats_and_projection(self):
        target = reference("EPSG:3857").ExportToWkt()
        for kind in ("vector", "gpkg", "shp"):
            layer = inspect(self.files[kind])
            self.assertEqual(layer.kind, "vector")
            frame = render(layer, target=target, edge=200)
            self.assertEqual(len(frame.geometries), 1)
            self.assertGreater(frame.geometries[0]["coordinates"][0][0][0], 9000000)
        output = self.root/"vector_projected.gpkg"
        layer = inspect(self.files["vector"])
        reproject(layer, layer.selection, output, "EPSG:3857")
        result = inspect(output)
        self.assertTrue(reference(result.wkt).IsSame(reference("EPSG:3857")))

    def test_raster_reprojection_and_no_overwrite(self):
        layer = inspect(self.files["raster"])
        output = self.root/"raster_projected.tif"
        reproject(layer, {"bands": [2]}, output, "EPSG:3857")
        result = gdal.Open(str(output))
        self.assertEqual(result.RasterCount, 1)
        self.assertEqual(result.GetRasterBand(1).GetNoDataValue(), -9999)
        self.assertAlmostEqual(result.GetRasterBand(1).GetScale(), .1)
        self.assertAlmostEqual(result.GetRasterBand(1).GetOffset(), 250)
        result = None
        with self.assertRaises(FileExistsError):
            reproject(layer, {"bands": [1]}, output, "EPSG:3857")

    def test_nc_full_resolution_reprojection(self):
        layer = inspect(self.files["nc"])
        output = self.root/"nc_projected.tif"
        selection = {"variable": "temperature", "indices": {"time": 1, "level": 1}}
        reproject(layer, selection, output, "EPSG:4326")
        result = gdal.Open(str(output))
        self.assertEqual((result.RasterXSize, result.RasterYSize), (72, 25))
        physical = result.ReadAsArray()
        self.assertAlmostEqual(float(physical[0, 36]), (3*1800+24*72)*.01+250)
        self.assertEqual(result.GetRasterBand(1).GetUnitType(), "K")
        result = None

    def test_exact_statistics_and_source_unchanged(self):
        layer = inspect(self.files["raster"])
        before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.root.glob("demo_monthly*")}
        expected = gdal.Open(layer.path).ReadAsArray()[0].astype(float)
        expected = expected[expected != -9999]*.1+250
        result = exact_statistics(layer, {"bands": [1]})["统计"]["1"]
        self.assertEqual(result["有效像元"], expected.size)
        self.assertAlmostEqual(result["均值"], float(expected.mean()))
        self.assertAlmostEqual(result["标准差"], float(expected.std()), places=6)
        after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.root.glob("demo_monthly*")}
        self.assertEqual(before, after)

    def test_cancellation_removes_partial_output(self):
        layer = inspect(self.files["nc"])
        cancel = threading.Event()
        cancel.set()
        output = self.root/"cancelled.tif"
        with self.assertRaises((Cancelled, RuntimeError)):
            reproject(layer, layer.selection, output, "EPSG:3857", cancel=cancel)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".geoquickview-*")))

    def test_map_export_offline_png_pdf(self):
        layer = inspect(self.files["raster"])
        vector = inspect(self.files["vector"])
        entries = [(layer, {"bands": [1]}, {"lower": 278, "upper": 330, "cmap": "viridis"}),
                   (vector, vector.selection, {"line": "#be123c"})]
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            for suffix in ("png", "pdf"):
                output = self.root/f"map.{suffix}"
                export_map(entries, layer.extent, layer.wkt, output, title="合成研究区 / Synthetic", dpi=100)
                self.assertGreater(output.stat().st_size, 5000)
                if suffix == "pdf":
                    self.assertTrue(output.read_bytes().startswith(b"%PDF"))
        from PIL import Image
        with Image.open(self.root/"map.png") as image:
            self.assertEqual(image.size, (787, 590))

    def test_unknown_crs_and_rotated_affine(self):
        path = self.root/"unreferenced.tif"
        ds = gdal.GetDriverByName("GTiff").Create(str(path), 12, 8, 1)
        ds.GetRasterBand(1).WriteArray(np.ones((8, 12), dtype=np.uint8))
        ds = None
        layer = inspect(path)
        self.assertFalse(layer.wkt)
        self.assertEqual(render(layer, edge=32).data.shape, (21, 32))
        with self.assertRaises(ValueError):
            reproject(layer, layer.selection, self.root/"invalid.tif", "EPSG:3857")
        rotated = self.root/"rotated.tif"
        ds = gdal.GetDriverByName("GTiff").Create(str(rotated), 10, 10, 1)
        ds.SetProjection(reference("EPSG:3857").ExportToWkt())
        ds.SetGeoTransform((100, 2, .5, 200, .5, -2))
        ds.GetRasterBand(1).WriteArray(np.arange(100, dtype=np.uint8).reshape(10, 10))
        ds = None
        layer = inspect(rotated)
        result = query(layer, layer.selection, *gdal.ApplyGeoTransform((100, 2, .5, 200, .5, -2), 3.5, 4.5), layer.wkt)
        self.assertEqual(result["波段 1"]["原始值"], 43)


    def test_unicode_directory_netcdf4(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/"中文目录_气象"
            files = create_samples(root)
            original = Path(files["nc"])
            renamed = original.with_name("温度_多变量.nc")
            original.rename(renamed)
            layer = inspect(renamed)
            value = query(layer, {"variable": "temperature", "indices": {"time": 1, "level": 1}},
                          -175, -5, reference("EPSG:4326").ExportToWkt())
            self.assertAlmostEqual(value["物理值"], 312.29)
            self.assertTrue(np.isfinite(render(layer, target=layer.wkt, edge=64).data).any())

    def test_unicode_classic_netcdf3(self):
        from scipy.io import netcdf_file
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"经典气象.nc"
            with netcdf_file(path, "w", version=2) as ds:
                ds.createDimension("lat", 10)
                ds.createDimension("lon", 20)
                for name, values, units in (("lat", np.arange(10), "degrees_north"), ("lon", np.arange(20), "degrees_east")):
                    coord = ds.createVariable(name, "f8", (name,))
                    coord[:] = values
                    coord.units = units
                var = ds.createVariable("temperature", "f4", ("lat", "lon"))
                var[:] = np.arange(200, dtype=np.float32).reshape(10, 20)
                var.units = "K"
            layer = inspect(path)
            result = query(layer, layer.selection, 5, 4, layer.wkt)
            self.assertAlmostEqual(result["物理值"], 85.)
            self.assertEqual(render(layer, target=layer.wkt, edge=64).unit, "K")


    def test_changed_source_is_not_silently_mixed_with_old_metadata(self):
        import shutil
        copy = self.root/"changed_source.tif"
        shutil.copy2(self.files["raster"], copy)
        layer = inspect(copy)
        with copy.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "已变化"):
            render(layer, target=layer.wkt)

    def test_netcdf_packed_valid_range(self):
        import h5netcdf
        with tempfile.TemporaryDirectory() as directory:
            files = create_samples(directory)
            with h5netcdf.File(files["nc"], "a") as ds:
                ds["temperature"].attrs["valid_range"] = np.array([0, 100], dtype=np.int16)
            layer = inspect(files["nc"])
            result = query(layer, layer.selection, 15, -50, layer.wkt)
            self.assertEqual(result["原始值"], 147)
            self.assertIsNone(result["物理值"])
            stats = exact_statistics(layer, layer.selection)
            self.assertEqual(stats["有效像元"], 100)
            self.assertAlmostEqual(stats["最大值"], 251.)


    def test_nc_duplicate_longitude_endpoint_previews(self):
        import h5netcdf
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"longitude_endpoint.nc"
            lat = np.arange(-60, 61, 5, dtype=float)
            lon = np.arange(0, 361, 5, dtype=float)
            data = np.arange(len(lat)*len(lon), dtype=np.float32).reshape(len(lat), len(lon))
            data[:, -1] = data[:, 0]  # the stored 360° column repeats the physical 0° column
            with h5netcdf.File(path, "w") as ds:
                ds.attrs["Conventions"] = "CF-1.8"
                ds.dimensions = {"lat": len(lat), "lon": len(lon)}
                for name, values, units, axis in (("lat", lat, "degrees_north", "Y"),
                                                  ("lon", lon, "degrees_east", "X")):
                    coordinate = ds.create_variable(name, (name,), dtype="f8", data=values)
                    coordinate.attrs.update(units=units, axis=axis)
                var = ds.create_variable("temperature", ("lat", "lon"), dtype="f4", data=data)
                var.attrs["units"] = "K"
            layer = inspect(path)
            grid = grid_info(layer.path, "temperature")
            self.assertTrue(grid["regular"])
            self.assertTrue(grid["merged"])
            self.assertEqual(len(grid["x"]), len(lon)-1)
            frame = render(layer, target=layer.wkt, edge=72)
            self.assertAlmostEqual(float(np.nanmin(frame.data)), 0.0)
            self.assertAlmostEqual(float(np.nanmax(frame.data)), float(data.max()))
            value = query(layer, layer.selection, 0.0, 0.0, layer.wkt)
            self.assertEqual(value["原始列"], 0)
            self.assertEqual(value["物理值"], float(data[len(lat)//2, 0]))
            self.assertIn("规则网格", layer.metadata["定位状态"])
            self.assertIn("合并", layer.metadata["网格说明"])

    def test_nc_nonuniform_latitude_previews_over_whole_field(self):
        import h5netcdf
        lat = np.array([-60, -50, -35, -20, 0, 20, 35, 50, 60], dtype=float)
        lon = np.arange(0, 10, 1, dtype=float)
        data = np.arange(len(lat)*len(lon), dtype=np.float32).reshape(len(lat), len(lon))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"stretched_latitude.nc"
            with h5netcdf.File(path, "w") as ds:
                ds.attrs["Conventions"] = "CF-1.8"
                ds.dimensions = {"lat": len(lat), "lon": len(lon)}
                for name, values, units, axis in (("lat", lat, "degrees_north", "Y"),
                                                  ("lon", lon, "degrees_east", "X")):
                    coordinate = ds.create_variable(name, (name,), dtype="f8", data=values)
                    coordinate.attrs.update(units=units, axis=axis)
                var = ds.create_variable("temperature", ("lat", "lon"), dtype="f4", data=data)
                var.attrs["units"] = "K"
            layer = inspect(path)
            grid = grid_info(layer.path, "temperature")
            self.assertFalse(grid["regular"])
            self.assertEqual(len(grid["ry"]), len(lat))
            self.assertIn("非均匀", layer.metadata["定位状态"])
            frame = render(layer, target=layer.wkt, edge=64)
            finite = frame.data[np.isfinite(frame.data)]
            self.assertGreater(finite.size, 0)
            self.assertGreaterEqual(float(finite.min()), float(data.min()))
            self.assertLessEqual(float(finite.max()), float(data.max()))
            # The sampled preview still reflects the whole field, not one band of it.
            self.assertGreater(float(finite.max()-finite.min()), .8*float(data.max()-data.min()))
            value = query(layer, layer.selection, 4.0, 0.0, layer.wkt)
            self.assertEqual(value["像元中心"], [4.0, 0.0])
            self.assertEqual(value["物理值"], float(data[len(lat)//2, 4]))
            with self.assertRaisesRegex(ValueError, "均匀"):
                reproject(layer, layer.selection, self.root/"nonuniform.tif", "EPSG:4326")

    def test_global_netcdf_edge_cells_are_queryable_across_longitude_wrap(self):
        layer = inspect(self.files["nc"])
        for longitude in (-182.4, 177.6, 537.6):
            result = query(layer, layer.selection, longitude, -5, layer.wkt)
            self.assertEqual(result["原始列"], 36)
            self.assertAlmostEqual(result["物理值"], 258.28)


    def test_rgb_overlay_does_not_export_an_unrelated_scalar_colorbar(self):
        from matplotlib.figure import Figure
        layer = inspect(self.files["raster"])
        entries = [(layer, {"bands": [1]}, {"lower": 278, "upper": 330}),
                   (layer, {"bands": [1, 2, 3]}, {"lower": 278, "upper": 330})]
        with patch.object(Figure, "colorbar") as colorbar:
            export_map(entries, layer.extent, layer.wkt, self.root/"rgb-overlay.png", dpi=100)
            colorbar.assert_not_called()


    def test_nc_prefers_spatial_data_over_auxiliary_arrays(self):
        from tests.nc_cases import nc_cases
        with tempfile.TemporaryDirectory() as directory:
            cases = nc_cases(directory)
            layer = inspect(cases["auxiliary"])
            self.assertEqual(layer.selection["variable"], "temperature")
            self.assertTrue(reference(layer.wkt).IsGeographic())
            self.assertTrue(np.isfinite(render(layer, edge=64).data).any())
            with self.assertRaises(ValueError):
                render(layer, target="", edge=64)

    def test_netcdf_without_2d_data_still_opens_attributes(self):
        from tests.nc_cases import nc_cases
        with tempfile.TemporaryDirectory() as directory:
            layer = inspect(nc_cases(directory)["profile"])
            self.assertEqual(layer.choices, [])
            self.assertEqual(layer.metadata["维度"]["time"], 3)
            self.assertEqual(layer.metadata["变量"]["temperature"]["属性"]["units"], "K")
            with self.assertRaisesRegex(ValueError, "属性"):
                render(layer, edge=64)

    def test_bundled_province_basemap_is_offline_and_projectable(self):
        from geoquickview.data import change_extent
        path = Path(geoquickview.__file__).with_name("assets")/"china_provinces.gpkg"
        layer = inspect(path)
        self.assertEqual(layer.kind, "vector")
        self.assertEqual(exact_statistics(layer, layer.selection)["要素数"], 34)
        self.assertTrue(reference(layer.wkt).IsProjected())
        target = reference("EPSG:4326").ExportToWkt()
        extent = change_extent(layer.extent, layer.wkt, target)
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            frame = render(layer, target=target, extent=extent, edge=512)
        self.assertEqual(len(frame.geometries), 34)
        self.assertNotIn("不完整", frame.note)

    def test_proj_data_lookup_covers_source_and_frozen_layouts(self):
        """The frozen bundle keeps PROJ data next to _internal; source uses Library/share."""
        import geoquickview
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root/"bundle"/"_internal"/"Library"/"share"/"proj"
            legacy.mkdir(parents=True)
            (legacy/"proj.db").write_bytes(b"x")
            frozen = root/"bundle"/"_internal"
            self.assertEqual(geoquickview._proj_data(frozen.parent, frozen), legacy)
            shared = root/"bundle"/"share"/"proj"
            shared.mkdir(parents=True)
            (shared/"proj.db").write_bytes(b"x")
            self.assertEqual(geoquickview._proj_data(frozen.parent, frozen), shared)
            conda = root/".conda"
            conda_proj = conda/"Library"/"share"/"proj"
            conda_proj.mkdir(parents=True)
            (conda_proj/"proj.db").write_bytes(b"x")
            self.assertEqual(geoquickview._proj_data(conda.parent, conda), conda_proj)

    def test_non_ascii_runtime_path_does_not_break_proj_lookup(self):
        """PROJ cannot read proj.db through a non-ASCII path.

        The package must therefore hand PROJ only ASCII locations. On a Chinese
        install path it mirrors the data to an ASCII cache instead of pointing
        PROJ at the install directory, and the ambient PROJ_* variables must be
        dropped so unrelated leftovers cannot take over.
        """
        import os
        import subprocess
        import sys

        import geoquickview
        from geoquickview import _is_ascii

        root = Path(geoquickview.__file__).resolve().parent.parent
        if _is_ascii(root):
            self.skipTest("project lives on an ASCII path; non-ASCII case not exercised")

        pinned = {key: os.environ.get(key) for key in ("PROJ_DATA", "PROJ_LIB")}
        try:
            for key in ("PROJ_DATA", "PROJ_LIB"):
                value = os.environ.get(key)
                if value:
                    self.assertTrue(_is_ascii(value), f"{key} points at a non-ASCII path: {value}")
                    self.assertTrue((Path(value) / "proj.db").is_file(), f"{key} lacks proj.db: {value}")
            # A fresh interpreter must resolve CRS even if the parent shell had a
            # stale PROJ_DATA value pointing at unrelated software.
            script = (
                "import geoquickview\n"
                "from osgeo import osr, gdal\n"
                "gdal.UseExceptions()\n"
                "s = osr.SpatialReference(); s.SetFromUserInput('EPSG:4326')\n"
                "print(s.GetName())\n"
                "s = osr.SpatialReference(); s.SetFromUserInput('EPSG:3857')\n"
                "print(s.GetName())\n"
            )
            environment = dict(os.environ)
            environment["PROJ_DATA"] = str(root / "does-not-exist" / "proj")
            environment.pop("PROJ_LIB", None)
            result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                    cwd=str(root), env=environment, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn("WGS", result.stdout)
            self.assertIn("Pseudo-Mercator", result.stdout)
        finally:
            for key, value in pinned.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
