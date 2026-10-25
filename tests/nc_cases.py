"""Small NC containers for metadata-only and auxiliary-variable behaviour."""
from pathlib import Path
import h5netcdf
import numpy as np


def nc_cases(directory):
    directory = Path(directory)
    bounds = directory/"cf_auxiliary.nc"
    with h5netcdf.File(bounds, "w") as ds:
        ds.attrs["Conventions"] = "CF-1.8"
        ds.dimensions = {"time": 2, "lat": 4, "lon": 8, "nv": 2}
        ds.create_variable("auxiliary", ("time", "nv"), dtype="f4", data=[[0, 1], [1, 2]])
        for name, values, units, axis in (("time", [0, 24], "hours since 2026-01-01", "T"),
                                         ("lat", np.arange(20, 24), "degrees_north", "Y"),
                                         ("lon", np.arange(100, 108), "degrees_east", "X")):
            coordinate = ds.create_variable(name, (name,), dtype="f8", data=values)
            coordinate.attrs.update(units=units, axis=axis)
        temperature = ds.create_variable("temperature", ("time", "lat", "lon"), dtype="f4",
                                          data=np.arange(64).reshape(2, 4, 8)+270)
        temperature.attrs["units"] = "K"
    profile = directory/"profile_metadata.nc"
    with h5netcdf.File(profile, "w") as ds:
        ds.dimensions = {"time": 3}
        ds.attrs["title"] = "One-dimensional profile"
        ds.create_variable("time", ("time",), dtype="f8", data=[0, 1, 2])
        temperature = ds.create_variable("temperature", ("time",), dtype="f4", data=[270, 271, 272])
        temperature.attrs["units"] = "K"
    return {"auxiliary": str(bounds), "profile": str(profile)}
