"""Wrapper around reduce_dimension that resolves dimension name aliases.

STAC catalogs use inconsistent dimension names (DATE, time, X, Y, Lon, etc.)
while odc-stac normalizes xarray dims to (t, x, y, bands). Users writing
process graphs may use either convention. This wrapper maps common aliases
to the actual xarray dim name before calling the upstream implementation.
"""
import logging
from typing import Callable, Optional

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

    return _upstream_reduce_dimension(
        data=data, reducer=reducer, dimension=dimension, context=context
    )
