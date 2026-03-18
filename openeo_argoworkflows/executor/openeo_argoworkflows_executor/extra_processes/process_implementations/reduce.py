"""Wrapper around reduce_dimension that resolves dimension name aliases.

STAC catalogs use inconsistent dimension names (DATE, time, X, Y, Lon, etc.)
while odc-stac normalizes xarray dims to (t, x, y, bands). Users writing
process graphs may use either convention. This wrapper maps common aliases
to the actual xarray dim name before calling the upstream implementation.

Also fixes netCDF serialization of reduced_dimensions_min_values attrs
(numpy.datetime64 is not netCDF-serializable, so we convert to ISO string).
"""
import logging
from typing import Callable, Optional

import numpy as np
from openeo_processes_dask.process_implementations.cubes.reduce import (
    reduce_dimension as _upstream_reduce_dimension,
)
from openeo_processes_dask.process_implementations.data_model import RasterCube

__all__ = ["reduce_dimension"]

logger = logging.getLogger(__name__)

# Map common user-facing names to the xarray dim names produced by odc-stac
_DIMENSION_ALIASES = {
    # temporal
    "time": "t",
    "DATE": "t",
    "date": "t",
    "temporal": "t",
    # spatial x
    "X": "x",
    "E": "x",
    "Lon": "x",
    "lon": "x",
    "longitude": "x",
    # spatial y
    "Y": "y",
    "N": "y",
    "Lat": "y",
    "lat": "y",
    "latitude": "y",
    # bands
    "band": "bands",
}


def reduce_dimension(
    data: RasterCube,
    reducer: Callable,
    dimension: str,
    context: Optional[dict] = None,
) -> RasterCube:
    if dimension not in data.dims and dimension in _DIMENSION_ALIASES:
        resolved = _DIMENSION_ALIASES[dimension]
        if resolved in data.dims:
            logger.info(
                "Resolved dimension alias '%s' -> '%s' (available: %s)",
                dimension,
                resolved,
                list(data.dims),
            )
            dimension = resolved

    result = _upstream_reduce_dimension(
        data=data, reducer=reducer, dimension=dimension, context=context
    )

    # Fix netCDF serialization: numpy.datetime64 is not a valid netCDF attr type.
    # The upstream reduce_dimension stores min values in attrs for later use,
    # but save_result chokes on datetime64. Convert to ISO 8601 string.
    min_vals = result.attrs.get("reduced_dimensions_min_values")
    if min_vals:
        for key, val in min_vals.items():
            if isinstance(val, (np.datetime64, np.generic)):
                min_vals[key] = str(val)

    return result
