"""Offline map layout; no web tiles or implicit Natural Earth downloads."""
from pathlib import Path
import math
import os
import tempfile

import numpy as np
from .data import render, reference
from .display import limits, rgba, geometry_parts


def scale_bar(ax, extent, wkt, font_size):
    from pyproj import CRS
    from osgeo import osr
    source = reference(wkt)
    geographic = source.CloneGeogCS()
    geographic.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, geographic)
    geod = CRS.from_wkt(wkt).get_geod()
    x0, y0, x1, y1 = extent
    start, y = x0+(x1-x0)*.065, y0+(y1-y0)*.075
    end = start+(x1-x0)*.22
    a = transform.TransformPoint(start, y)
    def distance(x):
        b = transform.TransformPoint(x, y)
        return abs(geod.inv(a[0], a[1], b[0], b[1])[2])
    available = distance(end)
    if not np.isfinite(available) or available <= 0:
        return
    power = 10**math.floor(math.log10(available))
    length = max(n*power for n in (.1, .2, .5, 1, 2, 5) if n*power <= available)
    left, right = start, end
    for _ in range(32):
        mid = (left+right)/2
        if distance(mid) < length:
            left = mid
        else:
            right = mid
    ax.plot([start, right], [y, y], color="#172b3b", linewidth=2.2, zorder=99)
    tick = (y1-y0)*.006
    for x in (start, right):
        ax.plot([x, x], [y-tick, y+tick], color="#172b3b", linewidth=1, zorder=99)
    label = f"{length/1000:g} km" if length >= 1000 else f"{length:g} m"
    ax.text((start+right)/2, y+tick*2.5, label, ha="center", va="bottom",
            fontsize=font_size-1, zorder=100, bbox=dict(facecolor="white", alpha=.8, edgecolor="none", pad=1))


def export_map(entries, extent, wkt, destination, title="研究区地图", width_cm=20, height_cm=15,
               dpi=300, font_size=10, grid=True, scalebar=True, progress=None, cancel=None):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib import font_manager, rc_context, colormaps
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize

    destination = Path(destination).resolve()
    if destination.suffix.lower() not in (".png", ".pdf"):
        raise ValueError("地图只支持 PNG 或 PDF。")
    if destination.exists():
        raise FileExistsError("输出文件已存在，请使用新文件名。")
    if width_cm <= 0 or height_cm <= 0 or dpi < 72 or width_cm*height_cm*(dpi/2.54)**2 > 80000000:
        raise ValueError("请使用有效页面尺寸、至少 72 DPI，且总像素不超过 8000 万。")
    families = {font.name for font in font_manager.fontManager.ttflist}
    font = next((name for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC") if name in families), "DejaVu Sans")
    with rc_context({"font.family": font, "axes.unicode_minus": False, "pdf.fonttype": 42, "font.size": font_size}):
        figure = Figure(figsize=(width_cm/2.54, height_cm/2.54), dpi=dpi, facecolor="white")
        if wkt:
            import cartopy.crs as ccrs
            from pyproj import CRS
            projection = ccrs.Projection(CRS.from_wkt(wkt))
            projection.bounds = (extent[0], extent[2], extent[1], extent[3])
            projection.threshold = min(extent[2]-extent[0], extent[3]-extent[1])/1000
            ax = figure.add_axes((.11, .13, .73, .75), projection=projection)
            ax.set_xlim(extent[0], extent[2])
            ax.set_ylim(extent[1], extent[3])
            if grid:
                lines = ax.gridlines(draw_labels=True, linewidth=.5, color="#718096", alpha=.5,
                                     x_inline=False, y_inline=False)
                lines.top_labels = False
                lines.right_labels = False
                lines.xlabel_style = lines.ylabel_style = {"size": font_size-1}
        else:
            ax = figure.add_axes((.11, .13, .73, .75))
            ax.set_xlim(extent[0], extent[2])
            ax.set_ylim(extent[1], extent[3])
            ax.set_aspect("equal")
            ax.set_xlabel("X（局部坐标）")
            ax.set_ylabel("Y（局部坐标）")
            ax.grid(grid, alpha=.25)
        scalar = None
        for index, (layer, selection, style) in enumerate(entries):
            if cancel and cancel.is_set():
                from .data import Cancelled
                raise Cancelled("已取消出图")
            frame = render(layer, selection, target=wkt, extent=extent,
                           edge=min(6000, max(256, round(max(width_cm, height_cm)/2.54*dpi))))
            if "不完整" in frame.note:
                raise ValueError("当前矢量超过出图上限，请缩小范围或筛选图层后导出，避免生成不完整地图。")
            opacity = style.get("opacity", 1.)
            if frame.data is not None:
                lo, hi = limits(frame.data, style.get("lower"), style.get("upper"))
                colour = style.get("cmap", "viridis")
                ax.imshow(rgba(frame.data, colour, lo, hi), origin="upper",
                          extent=(extent[0], extent[2], extent[1], extent[3]), alpha=opacity,
                          interpolation="nearest", zorder=index+1)
                if frame.data.ndim == 2:
                    scalar = (ScalarMappable(Normalize(lo, hi), colormaps[colour]), frame.unit, layer.name)
            else:
                first = True
                for geometry in frame.geometries:
                    for kind, values in geometry_parts(geometry):
                        if not len(values):
                            continue
                        ax.plot(values[:, 0], values[:, 1], color=style.get("line", "#1c9ea5"),
                                linewidth=.7, marker="." if kind == "point" else None,
                                linestyle="None" if kind == "point" else "-", alpha=opacity,
                                label=layer.name if first else None, zorder=index+1)
                        first = False
            if progress:
                progress((index+1)/(len(entries)+1))
        if scalar:
            mappable, unit, label = scalar
            cax = figure.add_axes((.88, .2, .025, .57))
            figure.colorbar(mappable, cax=cax, label=unit or "数值")
        ax.set_title(title, fontsize=font_size+4, pad=16, fontweight="bold")
        if scalebar and wkt:
            scale_bar(ax, extent, wkt, font_size)
        figure.text(.11, .035, "GeoQuickView  ·  "+("地理坐标已定位" if wkt else "坐标系未知：未绘制地理比例尺"),
                    fontsize=max(6, font_size-2), color="#64748b")
        fd, temporary = tempfile.mkstemp(prefix=".geoquickview-map-", suffix=destination.suffix, dir=destination.parent)
        os.close(fd)
        try:
            figure.savefig(temporary, dpi=dpi, facecolor="white")
            if cancel and cancel.is_set():
                from .data import Cancelled
                raise Cancelled("已取消出图")
            if destination.exists():
                raise FileExistsError("输出文件已存在。")
            os.rename(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
            figure.clear()
    if progress:
        progress(1.)
    return str(destination)
