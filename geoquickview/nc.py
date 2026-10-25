"""NetCDF metadata and lazy, coordinate-aware two-dimensional slices."""
from pathlib import Path
import math
import numpy as np
import xarray as xr
from osgeo import gdal
from .data import Layer, Frame, Cancelled, reference, change_extent, dimensions, _warp_frame


def dataset(path, raw=False):
    # h5py uses Unicode-safe Windows paths. SciPy handles classic CDF via Python I/O.
    with open(path, "rb") as stream:
        signature = stream.read(4)
    if signature == b"CDF\x05":
        raise ValueError("当前支持 NetCDF-3 classic/64-bit offset 与 NetCDF-4；CDF-5 暂不支持。")
    engine = "scipy" if signature.startswith(b"CDF") else "h5netcdf"
    return xr.open_dataset(path, engine=engine, decode_times=False,
                           mask_and_scale=not raw, create_default_indexes=False, cache=False)


def _axis(ds, var, which):
    aliases = ("lon", "longitude", "x") if which == "x" else ("lat", "latitude", "y")
    standards = ("longitude", "projection_x_coordinate") if which == "x" else ("latitude", "projection_y_coordinate")
    for name in list(var.coords) + list(var.dims):
        if name not in ds:
            continue
        coord = ds[name]
        if name.lower() in aliases or coord.attrs.get("axis", "").lower() == which or coord.attrs.get("standard_name") in standards:
            if coord.ndim != 1:
                raise ValueError("当前变量使用曲线/非结构坐标；可查看属性，首版地图预览需要规则的一维坐标。")
            if coord.dims[0] in var.dims:
                return name, coord.dims[0]
    return None, var.dims[-1 if which == "x" else -2]


def _grid(ds, variable):
    var = ds[variable]
    xn, xd = _axis(ds, var, "x")
    yn, yd = _axis(ds, var, "y")
    if xd == yd or len(var.dims) < 2:
        raise ValueError("无法识别两个不同的空间维度。")
    x = np.asarray(ds[xn].values, dtype=float) if xn else np.arange(var.sizes[xd], dtype=float)
    y = np.asarray(ds[yn].values, dtype=float) if yn else -np.arange(var.sizes[yd], dtype=float)
    wkt = ""
    mapping = var.attrs.get("grid_mapping", "")
    if mapping in ds:
        attrs = ds[mapping].attrs
        value = attrs.get("spatial_ref") or attrs.get("crs_wkt")
        if value:
            wkt = reference(value).ExportToWkt()
        elif "grid_mapping_name" in attrs:
            from pyproj import CRS
            wkt = CRS.from_cf(attrs).to_wkt()
    longitude = bool(xn and (xn.lower() in ("lon", "longitude") or ds[xn].attrs.get("standard_name") == "longitude"
                             or "degrees_east" in ds[xn].attrs.get("units", "").lower()))
    latitude = bool(yn and (yn.lower() in ("lat", "latitude") or ds[yn].attrs.get("standard_name") == "latitude"
                            or "degrees_north" in ds[yn].attrs.get("units", "").lower()))
    if longitude and latitude:
        wkt = wkt or reference("EPSG:4326").ExportToWkt()
        x = (x + 180) % 360 - 180
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("空间坐标包含无效值。")
    xo, yo = np.argsort(x), np.argsort(-y)
    x, y = x[xo], y[yo]
    if len(x) < 2 or len(y) < 2:
        raise ValueError("地图预览需要每个空间轴至少两个坐标。")
    dx, dy = float(np.median(np.diff(x))), float(-np.median(np.diff(y)))
    if dx <= 0 or dy <= 0 or not np.allclose(np.diff(x), dx, rtol=1e-3, atol=1e-7) or not np.allclose(np.diff(y), -dy, rtol=1e-3, atol=1e-7):
        raise ValueError("坐标不是均匀规则网格（或经度含重复端点）；可查看属性，不能按仿射栅格绘制。")
    return {"x": x, "y": y, "xo": xo, "yo": yo, "xd": xd, "yd": yd, "dx": dx, "dy": dy,
            "wkt": wkt, "extent": (x[0]-dx/2, y[-1]-dy/2, x[-1]+dx/2, y[0]+dy/2),
            "extra": {d: var.sizes[d] for d in var.dims if d not in (xd, yd)}}


def grid_info(path, variable):
    with dataset(path) as ds:
        return _grid(ds, variable)


def inspect_nc(path):
    with dataset(path, raw=True) as ds:
        variables = {name: {"维度": list(var.dims), "大小": list(var.shape), "数据类型": str(var.dtype),
                            "属性": dict(var.attrs)}
                     for name, var in ds.variables.items()}
        choices = [name for name in ds.data_vars if ds[name].ndim >= 2 and np.issubdtype(ds[name].dtype, np.number)]
        preferred = None
        for name in choices:
            try:
                xn, _ = _axis(ds, ds[name], "x")
                yn, _ = _axis(ds, ds[name], "y")
                if xn and yn and _grid(ds, name)["wkt"]:
                    preferred = name
                    break
            except ValueError:
                continue
        metadata = {"文件": path, "磁盘大小（字节）": Path(path).stat().st_size, "格式": "NetCDF",
                    "维度": dict(ds.sizes), "数据变量数量": len(ds.data_vars), "全局属性": dict(ds.attrs), "变量": variables}
        selection = {"variable": preferred or (choices[0] if choices else None), "indices": {}}
        try:
            if selection["variable"] is None:
                raise ValueError("没有二维数值变量，当前仅查看属性。")
            grid = _grid(ds, selection["variable"])
            wkt, extent = grid["wkt"], grid["extent"]
            metadata["定位状态"] = "规则网格" if wkt else "空间参照未知，使用原坐标/数组坐标"
        except ValueError as exc:
            wkt, extent = "", None
            metadata["定位状态"] = str(exc)
        return Layer(path, "nc", Path(path).name, wkt, extent, metadata, choices, selection)


def _read(ds, variable, grid, indices, rows=None, cols=None, physical=True):
    var = ds[variable]
    select = {d: min(max(int(indices.get(d, 0)), 0), size-1) for d, size in grid["extra"].items()}
    select[grid["xd"]] = grid["xo"] if cols is None else grid["xo"][cols]
    select[grid["yd"]] = grid["yo"] if rows is None else grid["yo"][rows]
    values = np.asarray(var.isel(select).transpose(grid["yd"], grid["xd"]).values)
    if physical:
        valid_range = var.attrs.get("valid_range")
        low = valid_range[0] if valid_range is not None else var.attrs.get("valid_min")
        high = valid_range[1] if valid_range is not None else var.attrs.get("valid_max")
        if low is not None or high is not None:
            scale = float(var.encoding.get("scale_factor", 1))
            offset = float(var.encoding.get("add_offset", 0))
            if scale == 0:
                raise ValueError("缩放因子为 0，无法按打包数值的有效范围解释数据。")
            valid = np.ones(values.shape, dtype=bool)
            if low is not None:
                bound = float(low)*scale+offset
                valid &= values >= bound if scale > 0 else values <= bound
            if high is not None:
                bound = float(high)*scale+offset
                valid &= values <= bound if scale > 0 else values >= bound
            values = np.where(valid, values, np.nan)
    return values


def render_nc(layer, selection, target, extent, edge):
    with dataset(layer.path) as ds:
        grid = _grid(ds, selection["variable"])
        native = change_extent(extent, target, grid["wkt"])
        cols = np.flatnonzero((grid["x"] >= native[0]-grid["dx"]) & (grid["x"] <= native[2]+grid["dx"]))
        rows = np.flatnonzero((grid["y"] >= native[1]-grid["dy"]) & (grid["y"] <= native[3]+grid["dy"]))
        if not len(cols) or not len(rows):
            return Frame(extent, target, np.full((2, 2), np.nan, dtype=np.float32), note="当前视窗没有数据")
        bounds = (grid["x"][cols[0]]-grid["dx"]/2, grid["y"][rows[-1]]-grid["dy"]/2,
                  grid["x"][cols[-1]]+grid["dx"]/2, grid["y"][rows[0]]+grid["dy"]/2)
        nx, ny = dimensions(bounds, edge)
        nx, ny = min(nx, len(cols)), min(ny, len(rows))
        ci = cols[np.minimum(((np.arange(nx)+.5)*len(cols)/nx).astype(int), len(cols)-1)]
        ri = rows[np.minimum(((np.arange(ny)+.5)*len(rows)/ny).astype(int), len(rows)-1)]
        values = _read(ds, selection["variable"], grid, selection.get("indices", {}), ri, ci).astype(np.float32)
        mem = gdal.GetDriverByName("MEM").Create("", nx, ny, 1, gdal.GDT_Float32)
        mem.SetGeoTransform((bounds[0], (bounds[2]-bounds[0])/nx, 0, bounds[3], 0, -(bounds[3]-bounds[1])/ny))
        mem.SetProjection(grid["wkt"])
        mem.GetRasterBand(1).SetNoDataValue(float("nan"))
        mem.GetRasterBand(1).SetUnitType(ds[selection["variable"]].attrs.get("units", ""))
        mem.GetRasterBand(1).WriteArray(values)
        frame = _warp_frame(mem, extent, target, edge, scaled=False)
        frame.note = "当前变量/切片的预览抽样范围（物理值）"
        return frame


def dimension_label(path, variable, dim, index):
    with dataset(path, raw=True) as ds:
        if dim not in ds:
            return str(index)
        coordinate = ds[dim]
        value = coordinate.isel({dim: index}).item()
        units = coordinate.attrs.get("units", "")
        if "since" in units:
            from cftime import num2date
            try:
                return str(num2date(value, units, calendar=coordinate.attrs.get("calendar", "standard")))
            except (ValueError, OverflowError):
                pass
        return f"{value} {units}".strip()


def query_nc(layer, selection, x, y):
    grid = grid_info(layer.path, selection["variable"])
    if reference(grid["wkt"]) and reference(grid["wkt"]).IsGeographic():
        left, right = grid["extent"][0], grid["extent"][2]
        if np.isclose(right-left, 360):
            x = (x-left) % 360 + left
        elif not left <= x <= right:
            x = (x+180) % 360-180
    if not (grid["extent"][0] <= x <= grid["extent"][2] and grid["extent"][1] <= y <= grid["extent"][3]):
        return {"位置": "图层范围外"}
    col, row = int(np.argmin(abs(grid["x"]-x))), int(np.argmin(abs(grid["y"]-y)))
    with dataset(layer.path, raw=True) as raw_ds, dataset(layer.path) as physical_ds:
        raw = _read(raw_ds, selection["variable"], grid, selection.get("indices", {}), [row], [col], physical=False).item()
        value = _read(physical_ds, selection["variable"], grid, selection.get("indices", {}), [row], [col]).item()
        return {"变量": selection["variable"], "原始列": int(grid["xo"][col]), "原始行": int(grid["yo"][row]),
                "像元中心": [float(grid["x"][col]), float(grid["y"][row])], "原始值": raw,
                "物理值": value if np.isfinite(value) else None, "单位": physical_ds[selection["variable"]].attrs.get("units", ""),
                "切片": selection.get("indices", {})}


def _blocks(layer, selection, cancel=None):
    with dataset(layer.path) as ds:
        grid = _grid(ds, selection["variable"])
        for start in range(0, len(grid["y"]), 128):
            if cancel and cancel.is_set():
                raise Cancelled("已取消")
            rows = np.arange(start, min(start+128, len(grid["y"])))
            yield start, _read(ds, selection["variable"], grid, selection.get("indices", {}), rows), grid


def statistics_nc(layer, selection, cancel=None, progress=None):
    count, mean, m2, low, high = 0, 0., 0., np.inf, -np.inf
    for start, block, grid in _blocks(layer, selection, cancel):
        values = block[np.isfinite(block)].astype(float)
        if values.size:
            n = values.size
            delta = values.mean()-mean
            m2 += ((values-values.mean())**2).sum() + delta**2*count*n/(count+n)
            mean += delta*n/(count+n)
            count += n
            low, high = min(low, values.min()), max(high, values.max())
        if progress:
            progress(min((start+len(block))/len(grid["y"]), 1))
    return {"范围": "当前变量和切片的全量精确统计（物理值）", "有效像元": int(count),
            "最小值": float(low) if count else None, "最大值": float(high) if count else None,
            "均值": float(mean) if count else None, "标准差": math.sqrt(m2/count) if count else None}


def materialize_nc(layer, selection, path, cancel=None):
    grid = grid_info(layer.path, selection["variable"])
    ds = gdal.GetDriverByName("GTiff").Create(str(path), len(grid["x"]), len(grid["y"]), 1,
                                             gdal.GDT_Float64, ["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform((grid["extent"][0], grid["dx"], 0, grid["extent"][3], 0, -grid["dy"]))
    ds.SetProjection(grid["wkt"])
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(float("nan"))
    band.SetDescription(selection["variable"])
    with dataset(layer.path) as nc:
        band.SetUnitType(nc[selection["variable"]].attrs.get("units", ""))
    try:
        for start, values, _ in _blocks(layer, selection, cancel):
            band.WriteArray(values.astype(np.float64), 0, start)
        ds.FlushCache()
    finally:
        band = None
        ds = None
    return str(path)
