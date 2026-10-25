"""Shared colour limits: preview, histogram and map export use identical values."""
import numpy as np


def limits(data, lower=None, upper=None):
    finite = np.asarray(data)[np.isfinite(data)]
    if lower is None or upper is None:
        if finite.size:
            lo, hi = np.percentile(finite[::max(1, finite.size//200000)], [2, 98])
        else:
            lo, hi = 0., 1.
        lower = lo if lower is None else lower
        upper = hi if upper is None else upper
    if upper <= lower:
        upper = lower + max(abs(lower)*.01, 1.)
    return float(lower), float(upper)


def rgba(data, cmap="viridis", lower=None, upper=None):
    from matplotlib import colormaps
    lower, upper = limits(data, lower, upper)
    scaled = np.clip((np.nan_to_num(data, nan=lower)-lower)/(upper-lower), 0, 1)
    if data.ndim == 3:
        alpha = np.isfinite(data).all(axis=-1)
        rgb = (scaled[..., :3]*255).astype(np.uint8)
        return np.dstack((rgb, (alpha*255).astype(np.uint8)))
    result = colormaps[cmap](scaled, bytes=True)
    result[..., 3] = np.isfinite(data)*255
    return result


def geometry_parts(geometry):
    kind, coords = geometry.get("type"), geometry.get("coordinates", [])
    if kind == "Point":
        yield "point", np.asarray([coords])[:, :2]
    elif kind == "MultiPoint":
        yield "point", np.asarray(coords)[:, :2]
    elif kind == "LineString":
        yield "line", np.asarray(coords)[:, :2]
    elif kind in ("Polygon", "MultiLineString"):
        for part in coords:
            yield "line", np.asarray(part)[:, :2]
    elif kind == "MultiPolygon":
        for polygon in coords:
            for part in polygon:
                yield "line", np.asarray(part)[:, :2]
    elif kind == "GeometryCollection":
        for child in geometry.get("geometries", []):
            yield from geometry_parts(child)
