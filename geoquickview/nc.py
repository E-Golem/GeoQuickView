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
    aliases = ("lon", "longitude", "x", "xlong") if which == "x" else ("lat", "latitude", "y", "xlat")
    standards = ("longitude", "projection_x_coordinate") if which == "x" else ("latitude", "projection_y_coordinate")
    units = ("degrees_east", "degree_east") if which == "x" else ("degrees_north", "degree_north")
    for name in list(var.coords) + list(var.dims):
        if name not in ds:
            continue
        coord = ds[name]
        if coord.ndim > 3:
            continue
        if (name.lower() in aliases or coord.attrs.get("axis", "").lower() == which
                or coord.attrs.get("standard_name") in standards
                or coord.attrs.get("units", "").lower() in units):
            if coord.ndim == 1:
                if coord.dims[0] in var.dims:
                    return name, coord.dims[0]
            elif coord.ndim == 2 and all(dim in var.dims for dim in coord.dims):
                return name, coord.dims[-1 if which == "x" else -2]
            elif coord.ndim == 3 and all(dim in var.dims for dim in coord.dims[1:]):
                return name, coord.dims[-1 if which == "x" else -2]
    return None, var.dims[-1 if which == "x" else -2]


def _plane(coord):
    """Drop leading non-spatial dimensions (WRF often stores XLAT as Time,Y,X)."""
    values = coord
    for dim in coord.dims[:-2]:
        values = values.isel({dim: 0})
    return np.asarray(values.values, dtype=float)


WRF_EARTH_RADIUS = 6370000.0


def _wrf_grid(ds, var, xd, yd):
    """Reconstruct the grid of a WRF output from its global attributes.

    WRF files usually carry no coordinate variables; the grid is regular in the
    WRF map projection (MAP_PROJ) and its origin follows from the domain centre
    plus DX/DY. Returns (x, y, wkt, note) or None when the file is not WRF.
    """
    attrs = ds.attrs
    required = ("MAP_PROJ", "DX", "DY", "CEN_LAT", "CEN_LON")
    if any(key not in attrs for key in required):
        return None
    try:
        projection = int(attrs["MAP_PROJ"])
        dx, dy = float(attrs["DX"]), float(attrs["DY"])
        nx, ny = var.sizes[xd], var.sizes[yd]
        centre = (float(attrs["CEN_LON"]), float(attrs["CEN_LAT"]))
    except (TypeError, ValueError):
        return None
    if nx < 2 or ny < 2:
        return None
    if projection in (0, 6):  # regular latitude/longitude grid, DX/DY in degrees
        wkt = reference("EPSG:4326").ExportToWkt()
        x = centre[0] - (nx-1)*dx/2 + np.arange(nx)*dx
        y = centre[1] - (ny-1)*dy/2 + np.arange(ny)*dy
        return x, y, wkt, "WRF 经纬度网格（由全局属性推导）"
    templates = {
        1: "+proj=lcc +lat_1={TRUELAT1} +lat_2={TRUELAT2} +lat_0={MOAD_CEN_LAT} +lon_0={STAND_LON}",
        2: "+proj=stere +lat_0={pole}90 +lat_ts={TRUELAT1} +lon_0={STAND_LON}",
        3: "+proj=merc +lat_ts={TRUELAT1} +lon_0={STAND_LON}",
    }
    template = templates.get(projection)
    if template is None:
        return None
    values = {"TRUELAT1": attrs.get("TRUELAT1"), "TRUELAT2": attrs.get("TRUELAT2"),
              "STAND_LON": attrs.get("STAND_LON"), "MOAD_CEN_LAT": attrs.get("MOAD_CEN_LAT")}
    if any(values[key] is None for key in ("TRUELAT1", "STAND_LON")):
        return None
    values["TRUELAT2"] = values["TRUELAT1"] if values["TRUELAT2"] is None else values["TRUELAT2"]
    values["MOAD_CEN_LAT"] = centre[1] if values["MOAD_CEN_LAT"] is None else values["MOAD_CEN_LAT"]
    values = {key: float(value) for key, value in values.items()}
    values["pole"] = "-" if values["TRUELAT1"] < 0 else ""
    try:
        from pyproj import CRS, Transformer
        crs = CRS.from_proj4(template.format(**values) +
                             f" +x_0=0 +y_0=0 +a={WRF_EARTH_RADIUS} +b={WRF_EARTH_RADIUS} +units=m +no_defs")
        transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        cx, cy = transformer.transform(centre[0], centre[1])
        wkt = reference(crs.to_wkt()).ExportToWkt()
    except Exception:
        return None
    x = cx - (nx-1)*dx/2 + np.arange(nx)*dx
    y = cy - (ny-1)*dy/2 + np.arange(ny)*dy
    return x, y, wkt, "WRF 网格（坐标系与范围由全局属性推导）"


def _ordered(values, ascending):
    """Sort one coordinate axis, dropping an exactly repeated endpoint.

    A 0–360 longitude axis that also stores 360 repeats the physical 0° column
    after wrapping to -180–180; keeping both would make the axis look irregular.
    """
    order = np.argsort(values if ascending else -values, kind="stable")
    ordered = values[order]
    if len(ordered) > 1:
        gaps = np.diff(ordered) * (1.0 if ascending else -1.0)
        tolerance = max(float(np.median(np.abs(gaps))) * 1e-6, 1e-12)
        keep = np.concatenate(([True], gaps > tolerance))
        order, ordered = order[keep], ordered[keep]
    return ordered, order


def _axis_plan(centres, ascending):
    """Return preview centres, their source position, spacing and regularity.

    Irregular but monotonic one-dimensional coordinates (stretched latitude,
    merged duplicate endpoints) are reflected on an equally spaced preview axis
    so the overall field can still be drawn. Queries and statistics keep using
    the original coordinates.
    """
    gaps = np.diff(centres) * (1.0 if ascending else -1.0)
    spacing = float(np.median(gaps))
    regular = bool(len(gaps) and spacing > 0 and np.all(gaps > 0)
                   and np.allclose(gaps, spacing, rtol=1e-3, atol=1e-7))
    if regular:
        return centres.copy(), np.arange(len(centres)), spacing, True
    sign = 1.0 if ascending else -1.0
    signed = centres * sign
    edges = np.empty(len(centres) + 1)
    edges[1:-1] = (signed[:-1] + signed[1:]) / 2
    edges[0] = signed[0] - gaps[0] / 2
    edges[-1] = signed[-1] + gaps[-1] / 2
    spacing = (edges[-1] - edges[0]) / len(centres)
    target = edges[0] + spacing * (np.arange(len(centres)) + .5)
    position = np.rint(np.interp(target, signed, np.arange(len(centres)))).astype(int)
    return target * sign, position, spacing, False


def _grid(ds, variable):
    var = ds[variable]
    xn, xd = _axis(ds, var, "x")
    yn, yd = _axis(ds, var, "y")
    if xd == yd or len(var.dims) < 2:
        raise ValueError("无法识别两个不同的空间维度。")
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
    wrf = _wrf_grid(ds, var, xd, yd)
    curvilinear = bool(xn and yn and (ds[xn].ndim >= 2 or ds[yn].ndim >= 2)) and wrf is None
    if wrf is not None:
        x, y, wkt, note = wrf
    elif curvilinear:
        # WRF-style 2-D latitude/longitude: approximate the grid by its middle
        # row/column so the overall field can still be placed on a map.
        lon, lat = _plane(ds[xn]), _plane(ds[yn])
        ny, nx = var.sizes[yd], var.sizes[xd]
        if lon.shape == (nx, ny) and lat.shape == (nx, ny):
            lon, lat = lon.T, lat.T
        if lon.shape != (ny, nx) or lat.shape != (ny, nx):
            raise ValueError("二维经纬度坐标与变量网格不匹配，无法定位。")
        x = lon[ny//2, :]
        y = lat[:, nx//2]
        wkt = wkt or reference("EPSG:4326").ExportToWkt()
        note = "曲线网格（二维经纬度）：按中心行/列近似定位预览"
    else:
        x = np.asarray(ds[xn].values, dtype=float) if xn else np.arange(var.sizes[xd], dtype=float)
        y = np.asarray(ds[yn].values, dtype=float) if yn else -np.arange(var.sizes[yd], dtype=float)
        note = ""
        longitude = bool(xn and (xn.lower() in ("lon", "longitude") or ds[xn].attrs.get("standard_name") == "longitude"
                                 or "degrees_east" in ds[xn].attrs.get("units", "").lower()))
        latitude = bool(yn and (yn.lower() in ("lat", "latitude") or ds[yn].attrs.get("standard_name") == "latitude"
                                or "degrees_north" in ds[yn].attrs.get("units", "").lower()))
        if longitude and latitude:
            wkt = wkt or reference("EPSG:4326").ExportToWkt()
            x = (x + 180) % 360 - 180
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("空间坐标包含无效值。")
    x_total, y_total = len(x), len(y)
    x, xo = _ordered(x, True)
    y, yo = _ordered(y, False)
    if len(x) < 2 or len(y) < 2:
        raise ValueError("地图预览需要每个空间轴至少两个坐标。")
    rx, rxi, dx, x_regular = _axis_plan(x, True)
    ry, ryi, dy, y_regular = _axis_plan(y, False)
    return {"x": x, "y": y, "xo": xo, "yo": yo, "xd": xd, "yd": yd, "dx": dx, "dy": dy,
            "rx": rx, "ry": ry, "rxi": rxi, "ryi": ryi,
            "regular": x_regular and y_regular,
            "approximated": curvilinear,
            "wrf": wrf is not None,
            "index_coordinates": not (xn and yn) and wrf is None,
            "note": note,
            "merged": len(x) != x_total or len(y) != y_total,
            "wkt": wkt, "extent": (rx[0]-dx/2, ry[-1]-dy/2, rx[-1]+dx/2, ry[0]+dy/2),
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
            if grid.get("wrf"):
                metadata["定位状态"] = "WRF 网格：坐标系与范围由全局属性（MAP_PROJ/DX/DY/CEN_LAT/CEN_LON）推导"
            elif grid.get("approximated"):
                metadata["定位状态"] = "曲线网格：预览按中心行/列近似定位；查询与统计使用该近似网格"
            elif not grid["regular"]:
                metadata["定位状态"] = "非均匀网格：预览按等间距近似显示，像元查询与统计使用原始坐标"
            elif wkt:
                metadata["定位状态"] = "规则网格"
            else:
                metadata["定位状态"] = "空间参照未知，使用原坐标/数组坐标"
            if grid["merged"]:
                metadata["网格说明"] = "已合并重复的经度/纬度端点后再定位"
            elif grid.get("note"):
                metadata["网格说明"] = grid["note"]
            if grid.get("index_coordinates"):
                metadata["索引坐标"] = "是（没有经纬度或投影坐标变量，不能与地理底图叠加）"
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


def render_nc(layer, selection, target, extent, edge, cancel=None):
    with dataset(layer.path) as ds:
        grid = _grid(ds, selection["variable"])
        native = change_extent(extent, target, grid["wkt"])
        rx, ry, dx, dy = grid["rx"], grid["ry"], grid["dx"], grid["dy"]
        cols = np.flatnonzero((rx >= native[0]-dx) & (rx <= native[2]+dx))
        rows = np.flatnonzero((ry >= native[1]-dy) & (ry <= native[3]+dy))
        if not len(cols) or not len(rows):
            return Frame(extent, target, np.full((2, 2), np.nan, dtype=np.float32), note="当前视窗没有数据")
        bounds = (rx[cols[0]]-dx/2, ry[rows[-1]]-dy/2, rx[cols[-1]]+dx/2, ry[rows[0]]+dy/2)
        nx, ny = dimensions(bounds, edge)
        nx, ny = min(nx, len(cols)), min(ny, len(rows))
        ci = cols[np.minimum(((np.arange(nx)+.5)*len(cols)/nx).astype(int), len(cols)-1)]
        ri = rows[np.minimum(((np.arange(ny)+.5)*len(rows)/ny).astype(int), len(rows)-1)]
        if cancel and cancel.is_set():
            raise Cancelled("已取消预览")
        values = _read(ds, selection["variable"], grid, selection.get("indices", {}),
                       grid["ryi"][ri], grid["rxi"][ci]).astype(np.float32)
        mem = gdal.GetDriverByName("MEM").Create("", nx, ny, 1, gdal.GDT_Float32)
        mem.SetGeoTransform((bounds[0], (bounds[2]-bounds[0])/nx, 0, bounds[3], 0, -(bounds[3]-bounds[1])/ny))
        mem.SetProjection(grid["wkt"])
        mem.GetRasterBand(1).SetNoDataValue(float("nan"))
        mem.GetRasterBand(1).SetUnitType(ds[selection["variable"]].attrs.get("units", ""))
        mem.GetRasterBand(1).WriteArray(values)
        frame = _warp_frame(mem, extent, target, edge, scaled=False, cancel=cancel)
        frame.note = "当前变量/切片的预览抽样范围（物理值）"
        if not grid["regular"]:
            frame.note += "；坐标非均匀，预览按等间距近似显示，查询与统计仍用原始坐标"
        return frame


def dimension_label(path, variable, dim, index):
    with dataset(path, raw=True) as ds:
        if dim not in ds:
            # WRF stores its timestamps in a Times(Time, 19) character array
            # instead of a Time coordinate variable.
            if dim.lower() == "time" and "Times" in ds:
                values = np.asarray(ds["Times"].values)
                if values.ndim >= 1 and 0 <= index < len(values):
                    row = np.asarray(values[index]).ravel()
                    if row.dtype.kind == "S":
                        text = b"".join(row.tolist()).decode("utf-8", "replace")
                    elif row.dtype.kind == "U":
                        text = "".join(row.tolist())
                    else:
                        text = "".join(chr(int(code)) for code in row if int(code))
                    text = text.replace("\x00", "").strip()
                    if text:
                        return text
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
    if grid.get("approximated"):
        raise ValueError("曲线网格仅支持预览、查询与统计；重投影会改变像元定位。")
    if not grid["regular"]:
        raise ValueError("当前变量坐标不是均匀网格，重投影会改变像元定位；预览、查询与统计仍可使用。")
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
