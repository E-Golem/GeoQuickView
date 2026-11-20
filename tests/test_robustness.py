"""Robustness and security checks: malformed files, no writes, no temp residue, no network.

Malformed files are exercised in a child process on purpose: a crash inside a
native parser (GDAL, HDF5, SQLite) would kill that child and show up as a
non-zero exit code instead of taking the test runner down, and a hang is caught
by the subprocess timeout.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from geoquickview.data import exact_statistics, inspect, query, render, reproject
from geoquickview.export import export_map
from geoquickview.samples import create_samples

ROOT = Path(__file__).resolve().parents[1]

CHILD = r"""
import json
import sys
from unittest.mock import patch


def forbidden(*args, **kwargs):
    raise AssertionError("network access attempted")


report = {"step": "inspect"}
try:
    with patch("socket.socket.connect", forbidden), patch("socket.create_connection", forbidden):
        from geoquickview.data import exact_statistics, inspect, query, render
        layer = inspect(sys.argv[1])
        report["step"] = "render"
        render(layer, target=layer.wkt, edge=64)
        report["step"] = "query"
        query(layer, layer.selection, 0.0, 0.0, layer.wkt)
        report["step"] = "statistics"
        exact_statistics(layer, layer.selection)
    print(json.dumps({"ok": True, "step": report["step"]}))
except Exception as exc:
    print(json.dumps({"ok": False, "step": report["step"],
                      "type": type(exc).__name__, "message": str(exc)}))
"""


def digest(path):
    path = Path(path)
    data = path.read_bytes()
    stat = path.stat()
    return (hashlib.sha256(data).hexdigest(), stat.st_size, stat.st_mtime_ns)


def directory_state(directory):
    directory = Path(directory)
    return {item.name: digest(item) for item in sorted(directory.iterdir()) if item.is_file()}


def sidecars(directory):
    directory = Path(directory)
    leftovers = []
    for item in directory.iterdir():
        name = item.name
        if name.endswith((".aux.xml", ".ovr", ".slice.tif")) or name.startswith(".geoquickview-"):
            leftovers.append(name)
    return leftovers


class Robustness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.files = create_samples(cls.root)
        cls.corrupt = cls.root/"corrupt"
        cls.corrupt.mkdir()
        raster = Path(cls.files["raster"]).read_bytes()
        netcdf = Path(cls.files["nc"]).read_bytes()
        gpkg = Path(cls.files["gpkg"]).read_bytes()
        random_bytes = np.random.default_rng(7).integers(0, 256, 4096, dtype=np.uint8).tobytes()
        cls.cases = [
            ("empty.tif", b""),
            ("random.tif", random_bytes),
            ("truncated.tif", raster[: len(raster)//3]),
            ("broken.nc", random_bytes),
            ("truncated.nc", netcdf[:512]),
            ("empty.geojson", b""),
            ("invalid.geojson", b'{"type": "FeatureCollection", "features": ['),
            ("broken.gpkg", gpkg[:128]),
            ("random.xyz", random_bytes),
        ]
        cls.paths = []
        for name, payload in cls.cases:
            path = cls.corrupt/name
            path.write_bytes(payload)
            cls.paths.append((name, path))
        directory = cls.corrupt/"a_directory"
        directory.mkdir()
        cls.paths.append(("directory", directory))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def environment(self):
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        return environment

    def payload(self, result):
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertTrue(lines, f"child printed nothing: {result.stderr[-1500:]}")
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError:
            self.fail(f"child output is not JSON: {lines[-1][:400]}\n{result.stderr[-1500:]}")

    def test_malformed_files_fail_readably_without_side_effects(self):
        for name, path in self.paths:
            with self.subTest(input=name):
                before = directory_state(path.parent)
                result = subprocess.run([sys.executable, "-c", CHILD, str(path)], cwd=str(ROOT),
                                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                                        timeout=120, env=self.environment())
                self.assertEqual(result.returncode, 0,
                                 f"{name}: native parser crashed (exit {result.returncode})\n"
                                 f"{result.stderr[-2000:]}")
                payload = self.payload(result)
                self.assertFalse(payload["ok"], f"{name}: malformed file was accepted as valid")
                message = payload["message"].strip()
                self.assertTrue(message, f"{name}: empty error message")
                self.assertNotIn("Traceback (most recent call last)", message)
                self.assertNotIn("codec can't decode", message)
                self.assertNotIn("network access attempted", message)
                self.assertEqual(directory_state(path.parent), before,
                                 f"{name}: the input directory was modified")
                self.assertFalse(sidecars(path.parent), f"{name}: sidecar files were written")

    def test_valid_workflow_does_not_touch_inputs_or_leave_temporary_files(self):
        output = self.root/"outputs"
        output.mkdir(exist_ok=True)
        inputs = [Path(self.files[kind]) for kind in ("raster", "nc", "vector")]
        before = {str(path): digest(path) for path in inputs}
        listing = directory_state(self.root)
        raster, nc, vector = (inspect(self.files[kind]) for kind in ("raster", "nc", "vector"))
        render(raster, target=raster.wkt, edge=64)
        render(nc, target=nc.wkt, edge=64)
        render(vector, target=vector.wkt, edge=64)
        query(raster, {"bands": [1]}, 72.5, 52.5, raster.wkt)
        exact_statistics(raster, {"bands": [1]})
        export_map([(raster, {"bands": [1]}, {"lower": 278, "upper": 330})],
                   raster.extent, raster.wkt, output/"map.png", dpi=80)
        self.assertEqual({str(path): digest(path) for path in inputs}, before,
                         "viewing or exporting modified an input file")
        self.assertEqual(directory_state(self.root), listing, "previewing wrote next to the input files")
        self.assertEqual(sorted(item.name for item in output.iterdir()), ["map.png"])
        self.assertFalse(sidecars(self.root))
        self.assertFalse(sidecars(output))

    def test_reprojection_leaves_no_temporary_files(self):
        raster, nc = inspect(self.files["raster"]), inspect(self.files["nc"])
        output = self.root/"projected"
        output.mkdir(exist_ok=True)
        reproject(raster, {"bands": [1]}, output/"raster.tif", "EPSG:3857")
        reproject(nc, nc.selection, output/"nc.tif", "EPSG:4326")
        self.assertEqual(sorted(item.name for item in output.iterdir()), ["nc.tif", "raster.tif"])
        with self.assertRaises(Exception) as caught:
            reproject(raster, {"bands": [1]}, output/"bad.tif", "EPSG:999999")
        self.assertTrue(str(caught.exception).strip())
        self.assertFalse((output/"bad.tif").exists())
        self.assertEqual(sorted(item.name for item in output.iterdir()), ["nc.tif", "raster.tif"],
                         "a failed reprojection left temporary files")
        self.assertFalse(sidecars(output))

    def test_core_workflow_needs_no_network(self):
        raster, nc, vector = (inspect(self.files[kind]) for kind in ("raster", "nc", "vector"))
        output = self.root/"offline"
        output.mkdir(exist_ok=True)

        def forbidden(*args, **kwargs):
            raise AssertionError("network access attempted")

        with patch("socket.socket.connect", forbidden), \
             patch("socket.create_connection", forbidden), \
             patch("urllib.request.urlopen", forbidden):
            render(raster, target=raster.wkt, edge=64)
            render(nc, nc.selection, nc.wkt, edge=64)
            render(vector, vector.selection, vector.wkt, edge=64)
            exact_statistics(raster, {"bands": [1]})
            export_map([(raster, {"bands": [1]}, {"lower": 278, "upper": 330})],
                       raster.extent, raster.wkt, output/"map.png", dpi=80)
        self.assertGreater((output/"map.png").stat().st_size, 1000)

    def test_missing_files_and_directories_report_readable_errors(self):
        with self.assertRaises(ValueError) as caught:
            inspect(self.root/"does-not-exist.tif")
        self.assertIn("存在", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            inspect(self.corrupt)
        self.assertIn("存在", str(caught.exception))


if __name__ == "__main__":
    unittest.main()