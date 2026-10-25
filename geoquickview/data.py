"""GDAL-backed metadata, bounded previews, exact queries and reprojection.

Datasets are opened inside each operation and never shared between worker threads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import math
import os
import tempfile
import threading

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()
# Source handles remain read-only; do not call mutating GDAL statistics APIs on them.
gdal.SetCacheMax(128 * 1024 * 1024)


@dataclass
class Layer:
    path: str
    kind: str
    name: str
    wkt: str
    extent: tuple | None
    metadata: dict
    choices: list
    selection: dict = field(default_factory=dict)
    fingerprint: tuple = field(init=False, repr=False)

    def __post_init__(self):
        stat = Path(self.path).stat()
        self.fingerprint = (stat.st_size, stat.st_mtime_ns)

    def verify_source(self):
        stat = Path(self.path).stat()
        if (stat.st_size, stat.st_mtime_ns) != self.fingerprint:
            raise ValueError("输入文件已变化，请移除此图层后重新打开，避免使用过时属性。")


@dataclass
class Frame:
    extent: tuple
    wkt: str
    data: np.ndarray | None = None
    geometries: list = field(default_factory=list)
    note: str = ""
    unit: str = ""


class Cancelled(Exception):
    pass


def reference(value):
    if not value:
        return None
    result = osr.SpatialReference()
    result.SetFromUserInput(str(value))
    result.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return result


def crs_name(wkt):
    if not wkt:
        return "未知 / 局部坐标"
    sr = reference(wkt)
    name = sr.GetName()
    if not name or name.lower() in ("unknown", "unnamed"):
        name = sr.GetAttrValue("PROJECTION") or "自定义坐标系"
    authority = sr.GetAuthorityCode(None)
    return f"{name} (EPSG:{authority})" if authority else name



def coordinate_unit(wkt):
    sr = reference(wkt)
    if sr is None:
        return "未知 / 局部坐标"
    return sr.GetAngularUnitsName() if sr.IsGeographic() else sr.GetLinearUnitsName()


def change_extent(bounds, source, target):
    if not bounds or not source or not target:
        return bounds
    a, b = reference(source), reference(target)
    if a.IsSame(b):
        return bounds
    x0, y0, x1, y1 = bounds
    t = np.linspace(0, 1, 33)
    points = [(x0 + f * (x1 - x0), y) for y in (y0, y1) for f in t]
    points += [(x, y0 + f * (y1 - y0)) for x in (x0, x1) for f in t]
    transformed = np.asarray(osr.CoordinateTransformation(a, b).TransformPoints(points))[:, :2]
    transformed = transformed[np.isfinite(transformed).all(axis=1)]
    if not len(transformed):
        raise ValueError("无法转换图层范围，请检查坐标系。")
    return (*transformed.min(axis=0), *transformed.max(axis=0))


def dimensions(extent, max_edge):
    width, height = extent[2] - extent[0], extent[3] - extent[1]
    if width <= 0 or height <= 0:
        raise ValueError("地图范围必须具有正的宽度与高度。")
    scale = max(width, height)
    return max(1, round(max_edge * width / scale)), max(1, round(max_edge * height / scale))


def _raster_bounds(ds):
    gt = ds.GetGeoTransform(can_return_null=True)
    if gt is None:
        return (0, -ds.RasterYSize, ds.RasterXSize, 0)
    corners = [gdal.ApplyGeoTransform(gt, x, y) for x in (0, ds.RasterXSize) for y in (0, ds.RasterYSize)]
    return (min(p[0] for p in corners), min(p[1] for p in corners),
            max(p[0] for p in corners), max(p[1] for p in corners))


def band_info(path, index):
    ds = gdal.Open(path)
    band = ds.GetRasterBand(index)
    md = band.GetMetadata()
    return {
        "波段": index, "名称": band.GetDescription(),
        "数据类型": gdal.GetDataTypeName(band.DataType),
        "NoData": band.GetNoDataValue(), "单位": band.GetUnitType(),
        "缩放因子": band.GetScale(), "偏移量": band.GetOffset(),
        "概览数量": band.GetOverviewCount(), "块大小": band.GetBlockSize(),
        "已有统计（原始存储值，未经重新验证）": {
            k.removeprefix("STATISTICS_"): v for k, v in md.items() if k.startswith("STATISTICS_")
        },
    }


def inspect(path):
    path = str(Path(path).resolve())
    if not Path(path).is_file():
        raise ValueError("请选择存在的数据文件。")
    if Path(path).suffix.lower() in (".nc", ".nc4", ".cdf"):
        from .nc import inspect_nc
        return inspect_nc(path)
    vector = ogr.Open(path, 0) if gdal.IdentifyDriverEx(path, gdal.OF_VECTOR) else None
    if vector and vector.GetLayerCount():
        choices, metadata = [], {}
        for i in range(vector.GetLayerCount()):
            layer = vector.GetLayerByIndex(i)
            sr = layer.GetSpatialRef()
            extent = layer.GetExtent(force=0)
            choices.append(layer.GetName())
            definition = layer.GetLayerDefn()
            metadata[layer.GetName()] = {
                "坐标系": sr.ExportToWkt() if sr else "", "要素数（-1=待计算）": layer.GetFeatureCount(force=0),
                "几何类型": ogr.GeometryTypeToName(definition.GetGeomType()),
                "字段": [(definition.GetFieldDefn(j).GetName(), definition.GetFieldDefn(j).GetTypeName())
                         for j in range(definition.GetFieldCount())],
                "范围": (extent[0], extent[2], extent[1], extent[3]) if extent else None,
            }
        first = metadata[choices[0]]
        return Layer(path, "vector", Path(path).name, first["坐标系"], first["范围"],
                     {"文件": path, "主文件磁盘大小（字节）": Path(path).stat().st_size, "格式": vector.GetDriver().GetName(), "图层": metadata},
                     choices, {"layer": choices[0]})
    ds = gdal.OpenEx(path, gdal.OF_RASTER | gdal.OF_READONLY)
    if ds is None or not ds.RasterCount:
        raise ValueError("此文件没有可读取的栅格波段或矢量图层。")
    gt = ds.GetGeoTransform(can_return_null=True)
    wkt = ds.GetProjection()
    metadata = {
        "文件": path, "磁盘大小（字节）": Path(path).stat().st_size,
        "格式": ds.GetDriver().LongName, "宽 × 高": [ds.RasterXSize, ds.RasterYSize],
        "波段数量": ds.RasterCount, "坐标系": crs_name(wkt), "坐标系 WKT": wkt, "空间坐标单位": coordinate_unit(wkt),
        "范围": _raster_bounds(ds), "仿射变换": gt,
        "X/Y 分辨率（坐标系单位）": [math.hypot(gt[1], gt[4]), math.hypot(gt[2], gt[5])] if gt else None,
        "元数据": ds.GetMetadata(), "子数据集": ds.GetSubDatasets(),
        "当前波段属性": band_info(path, 1),
    }
    return Layer(path, "raster", Path(path).name, wkt, _raster_bounds(ds), metadata,
                 [f"{i}: {ds.GetRasterBand(i).GetDescription() or 'Band ' + str(i)}"
                  for i in range(1, ds.RasterCount + 1)], {"bands": [1]})


def layer_space(layer, selection=None):
    layer.verify_source()
    selection = selection or layer.selection
    if layer.kind == "nc":
        if not selection.get("variable"):
            return "", None
        from .nc import grid_info
        grid = grid_info(layer.path, selection["variable"])
        return grid["wkt"], grid["extent"]
    if layer.kind == "vector":
        md = layer.metadata["图层"][selection["layer"]]
        return md["坐标系"], md["范围"]
    return layer.wkt, layer.extent


def _selected_raster(layer, selection):
    ds = gdal.Open(layer.path)
    selected = gdal.Translate("", ds, format="VRT", bandList=selection["bands"])
    if selected.GetGeoTransform(can_return_null=True) is None:
        selected.SetGeoTransform((0, 1, 0, 0, 0, -1))
    return selected


def _warp_frame(ds, extent, target, edge, scaled=True):
    nx, ny = dimensions(extent, edge)
    kwargs = dict(format="MEM", outputBounds=extent, width=nx, height=ny,
                  resampleAlg="near", outputType=gdal.GDT_Float32, dstNodata=float("nan"))
    if target:
        kwargs["dstSRS"] = target
    out = gdal.Warp("", ds, **kwargs)
    channels = []
    for i in range(1, ds.RasterCount + 1):
        band = out.GetRasterBand(i)
        values = band.ReadAsArray()
        valid = band.GetMaskBand().ReadAsArray() > 0
        values = np.where(valid, values, np.nan).astype(np.float32)
        if scaled:
            src = ds.GetRasterBand(i)
            values = values * (src.GetScale() if src.GetScale() is not None else 1)
            values += src.GetOffset() or 0
        channels.append(values)
    data = channels[0] if len(channels) == 1 else np.stack(channels, axis=-1)
    return Frame(extent, target or ds.GetProjection(), data, note="预览抽样；色标为显示拉伸范围，全量值范围请用精确统计",
                 unit=ds.GetRasterBand(1).GetUnitType())


def render(layer, selection=None, target=None, extent=None, edge=1536):
    selection = selection or layer.selection
    wkt, bounds = layer_space(layer, selection)
    if layer.kind == "nc" and not selection.get("variable"):
        raise ValueError("没有二维数值变量，当前仅查看属性。")
    target = wkt if target is None else target
    if bool(wkt) != bool(target):
        raise ValueError("有空间参照与未知空间参照的图层不能在同一坐标画布中叠加。")
    extent = extent or change_extent(bounds, wkt, target)
    if extent is None:
        raise ValueError("图层没有可用范围。")
    if layer.kind == "vector":
        return render_vector(layer, selection, target, extent, edge)
    if layer.kind == "nc":
        from .nc import render_nc
        return render_nc(layer, selection, target, extent, edge)
    return _warp_frame(_selected_raster(layer, selection), extent, target, edge)


def render_vector(layer, selection, target, extent, edge):
    ds = ogr.Open(layer.path, 0)
    src = ds.GetLayerByName(selection["layer"])
    source = src.GetSpatialRef()
    if source:
        source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target_sr = reference(target)
    transform = osr.CoordinateTransformation(source, target_sr) if source and target_sr else None
    native = change_extent(extent, target, source.ExportToWkt() if source else "")
    src.SetSpatialFilterRect(*native)
    geometries = []
    # ponytail: bound interactive geometry work; zoom/filter before increasing this ceiling.
    feature_limit, point_limit = 50000, 1000000
    points, truncated = 0, False
    tolerance = (extent[2] - extent[0]) / edge * 0.35
    for feature in src:
        geometry = feature.GetGeometryRef()
        if geometry is None:
            continue
        geometry = geometry.Clone()
        if transform:
            geometry.Transform(transform)
        geometry = geometry.SimplifyPreserveTopology(tolerance)
        points += _point_count(geometry)
        if len(geometries) >= feature_limit or points > point_limit:
            truncated = True
            break
        geometries.append(json.loads(geometry.ExportToJson()))
    note = "矢量预览已简化（源数据未改动）"
    if truncated:
        note += "；达到预览上限，请放大查看，当前显示不完整"
    return Frame(extent, target, geometries=geometries, note=note)


def _point_count(geometry):
    return geometry.GetPointCount() + sum(_point_count(geometry.GetGeometryRef(i))
                                         for i in range(geometry.GetGeometryCount()))


def query(layer, selection, x, y, map_wkt):
    if layer.kind == "nc" and not selection.get("variable"):
        return {"说明": "此文件没有二维数值变量，当前仅查看属性。"}
    wkt, _ = layer_space(layer, selection)
    if map_wkt and wkt:
        x, y, _ = osr.CoordinateTransformation(reference(map_wkt), reference(wkt)).TransformPoint(x, y)
    elif bool(map_wkt) != bool(wkt):
        raise ValueError("坐标系未知，无法查询该叠加图层。")
    if layer.kind == "nc":
        from .nc import query_nc
        return query_nc(layer, selection, x, y)
    if layer.kind == "vector":
        ds = ogr.Open(layer.path, 0)
        src = ds.GetLayerByName(selection["layer"])
        tolerance = max((layer_space(layer, selection)[1][2] - layer_space(layer, selection)[1][0]) / 1500, 1e-9)
        src.SetSpatialFilterRect(x-tolerance, y-tolerance, x+tolerance, y+tolerance)
        matches = []
        for feature in src:
            matches.append({"FID": feature.GetFID(), **feature.items()})
            if len(matches) == 10:
                break
        return {"附近要素（最多 10 个）": matches}
    ds = gdal.Open(layer.path)
    inverse = gdal.InvGeoTransform(ds.GetGeoTransform(can_return_null=True) or (0, 1, 0, 0, 0, -1))
    column, row = gdal.ApplyGeoTransform(inverse, x, y)
    column, row = math.floor(column), math.floor(row)
    if not (0 <= column < ds.RasterXSize and 0 <= row < ds.RasterYSize):
        return {"位置": "图层范围外"}
    result = {"列": column, "行": row, "坐标": [x, y]}
    for index in selection["bands"]:
        band = ds.GetRasterBand(index)
        raw = band.ReadAsArray(column, row, 1, 1)[0, 0].item()
        mask = bool(band.GetMaskBand().ReadAsArray(column, row, 1, 1)[0, 0])
        valid = bool(mask and np.isfinite(raw))
        result[f"波段 {index}"] = {"原始值": raw, "有效": valid,
                                  "物理值": raw * (band.GetScale() if band.GetScale() is not None else 1) + (band.GetOffset() or 0) if valid else None,
                                  "单位": band.GetUnitType()}
    return result


def exact_statistics(layer, selection, cancel=None, progress=None):
    layer.verify_source()
    if layer.kind == "vector":
        ds = ogr.Open(layer.path, 0)
        src = ds.GetLayerByName(selection["layer"])
        return {"要素数": src.GetFeatureCount(force=1), "范围": src.GetExtent(force=1)}
    if layer.kind == "nc":
        if not selection.get("variable"):
            raise ValueError("精确栅格统计需要先选择一个二维变量；此文件可查看属性。")
        from .nc import statistics_nc
        return statistics_nc(layer, selection, cancel, progress)
    ds = _selected_raster(layer, selection)
    result = {}
    for i in range(1, ds.RasterCount+1):
        band = ds.GetRasterBand(i)
        count, mean, m2, minimum, maximum = 0, 0., 0., np.inf, -np.inf
        for row in range(0, ds.RasterYSize, 128):
            if cancel and cancel.is_set():
                raise Cancelled("已取消统计")
            height = min(128, ds.RasterYSize-row)
            data = band.ReadAsArray(0, row, ds.RasterXSize, height).astype(np.float64)
            mask = (band.GetMaskBand().ReadAsArray(0, row, ds.RasterXSize, height) > 0) & np.isfinite(data)
            values = data[mask] * (band.GetScale() if band.GetScale() is not None else 1) + (band.GetOffset() or 0)
            if values.size:
                size = values.size
                block_mean = values.mean()
                delta = block_mean - mean
                m2 += np.sum((values-block_mean)**2) + delta**2*count*size/(count+size)
                mean += delta*size/(count+size)
                count += size
                minimum, maximum = min(minimum, values.min()), max(maximum, values.max())
            if progress:
                progress((i-1+min(row+height, ds.RasterYSize)/ds.RasterYSize)/ds.RasterCount)
        result[str(selection["bands"][i-1])] = {
            "有效像元": int(count), "最小值": float(minimum) if count else None,
            "最大值": float(maximum) if count else None, "均值": float(mean) if count else None,
            "标准差": float(math.sqrt(max(0, m2/count))) if count else None,
        }
    return {"范围": "当前选择波段的全量精确统计（物理值）；未写入源文件", "统计": result}


def reproject(layer, selection, destination, target, resampling="near", cancel=None, progress=None):
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("输出文件已存在，请选择新文件名。")
    if not destination.parent.is_dir():
        raise ValueError("输出目录不存在。")
    target = reference(target).ExportToWkt()
    wkt, _ = layer_space(layer, selection)
    if not wkt:
        raise ValueError("源坐标系未知，不能执行重投影。")
    def callback(value, message, unused):
        if progress:
            progress(float(value))
        return 0 if cancel and cancel.is_set() else 1
    suffix = ".gpkg" if layer.kind == "vector" else ".tif"
    if destination.suffix.lower() != suffix:
        raise ValueError(f"此操作输出 {suffix}，请选择匹配的扩展名。")
    fd, temporary = tempfile.mkstemp(prefix=".geoquickview-", suffix=suffix, dir=destination.parent)
    os.close(fd)
    os.unlink(temporary)
    out = None
    try:
        if cancel and cancel.is_set():
            raise Cancelled("已取消重投影")
        if layer.kind == "vector":
            out = gdal.VectorTranslate(temporary, layer.path, format="GPKG", dstSRS=target,
                                       layers=[selection["layer"]], reproject=True, callback=callback)
        else:
            if layer.kind == "nc":
                from .nc import materialize_nc
                with tempfile.TemporaryDirectory(prefix="geoquickview-nc-") as directory:
                    source = materialize_nc(layer, selection, Path(directory)/"slice.tif", cancel)
                    out = gdal.Warp(temporary, source, format="GTiff", dstSRS=target, resampleAlg=resampling,
                                    creationOptions=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"], callback=callback)
            else:
                source = _selected_raster(layer, selection)
                out = gdal.Warp(temporary, source, format="GTiff", dstSRS=target, resampleAlg=resampling,
                                creationOptions=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"], callback=callback)
        if cancel and cancel.is_set():
            raise Cancelled("已取消重投影")
        if out is None:
            raise RuntimeError("重投影未产生结果。")
        out.FlushCache()
        out = None
        # Windows rename refuses to replace an existing destination (including a race).
        if destination.exists():
            raise FileExistsError("输出文件已存在。")
        os.rename(temporary, destination)
    except RuntimeError:
        if cancel and cancel.is_set():
            raise Cancelled("已取消重投影") from None
        raise
    finally:
        out = None
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(destination)
