from __future__ import annotations

import sys
import types

import numpy as np
import pytest
import xarray as xr
from openeo_pg_parser_networkx import OpenEOProcessGraph, ProcessRegistry
from openeo_processes_dask_slim.process_implementations.core import process


def _install_stactools_stub(monkeypatch):
    stactools = types.ModuleType("stactools")
    stactools_core = types.ModuleType("stactools.core")
    stactools_projection = types.ModuleType("stactools.core.projection")
    stactools_core.projection = stactools_projection
    stactools.core = stactools_core
    monkeypatch.setitem(sys.modules, "stactools", stactools)
    monkeypatch.setitem(sys.modules, "stactools.core", stactools_core)
    monkeypatch.setitem(sys.modules, "stactools.core.projection", stactools_projection)


def _install_dedl_cube_load_stub(monkeypatch, cube=None):
    package = types.ModuleType("openeo_processes_dedl_cube_load")
    specs = types.ModuleType("openeo_processes_dedl_cube_load.specs")

    def load_stac(url, **_):
        return cube

    def filter_dggs(
        data,
        extent=None,
        method=None,
        cells=None,
        predicate="intersects",
        k=0,
        dimension=None,
    ):
        del extent, method, predicate, k
        if cells is None:
            return data

        dim = dimension or "healpix_index"
        cell_values = np.asarray(data[dim].values)
        positions = np.flatnonzero(np.isin(cell_values, np.asarray(cells)))
        return data.isel({dim: positions})

    def dggs_to_raster(
        data,
        resolution,
        projection=4326,
        method="near",
        extent=None,
        spatial_extent=None,
        dimension=None,
    ):
        del extent, spatial_extent
        dim = dimension or "healpix_index"
        raster_data = data["temperature"].isel({dim: slice(0, 4)}).data.reshape(
            (1, 2, 2)
        )
        return xr.Dataset(
            {"temperature": (("t", "y", "x"), raster_data)},
            coords={
                "t": data["t"],
                "y": np.array([45.5, 44.5]),
                "x": np.array([10.5, 11.5]),
            },
            attrs={
                "crs": f"EPSG:{projection}",
                "method": method,
                "resolution": resolution,
            },
        )

    def raster_to_dggs(data, **_):
        return data

    for implementation in (
        load_stac,
        filter_dggs,
        raster_to_dggs,
        dggs_to_raster,
    ):
        process_name = implementation.__name__
        setattr(package, process_name, implementation)
        setattr(specs, process_name, {"id": process_name})

    package.specs = specs
    monkeypatch.setitem(sys.modules, "openeo_processes_dedl_cube_load", package)
    monkeypatch.setitem(sys.modules, "openeo_processes_dedl_cube_load.specs", specs)


def _make_dggs_cube():
    dask_array = pytest.importorskip("dask.array")
    values = dask_array.arange(4, chunks=(2,)).reshape((1, 4))
    return xr.Dataset(
        {"temperature": (("t", "healpix_index"), values)},
        coords={
            "t": np.array(["2024-01-01"], dtype="datetime64[ns]"),
            "healpix_index": np.array([0, 1, 2, 3], dtype=np.int64),
            "lat": ("healpix_index", np.array([45.25, 45.25, 44.75, 44.75])),
            "lon": ("healpix_index", np.array([10.25, 10.75, 10.25, 10.75])),
        },
        attrs={"grid": "healpix", "healpix_nside": 1},
    )


def _register_test_dedl_processes(monkeypatch, cube):
    _install_stactools_stub(monkeypatch)
    _install_dedl_cube_load_stub(monkeypatch, cube)

    from openeo_argoworkflows_executor.executor import (
        _register_dedl_cube_load_processes,
    )

    registry = ProcessRegistry(wrap_funcs=[process])
    _register_dedl_cube_load_processes(registry)
    return registry


def test_register_dedl_cube_load_processes(monkeypatch):
    _install_stactools_stub(monkeypatch)

    from openeo_argoworkflows_executor.executor import (
        _register_dedl_cube_load_processes,
    )

    _install_dedl_cube_load_stub(monkeypatch)

    registry = ProcessRegistry()

    _register_dedl_cube_load_processes(registry)

    for process_name in (
        "load_stac",
        "filter_dggs",
        "raster_to_dggs",
        "dggs_to_raster",
    ):
        assert process_name in registry
        assert registry[process_name].spec["id"] == process_name
        assert registry[process_name].implementation.__name__ == process_name


def test_filter_dggs_process_graph_executes_from_registered_process(monkeypatch):
    dask_array = pytest.importorskip("dask.array")
    registry = _register_test_dedl_processes(monkeypatch, _make_dggs_cube())

    graph = OpenEOProcessGraph(
        pg_data={
            "load": {
                "process_id": "load_stac",
                "arguments": {"url": "memory://healpix.zarr"},
            },
            "filter": {
                "process_id": "filter_dggs",
                "arguments": {
                    "data": {"from_node": "load"},
                    "cells": [1, 3],
                    "dimension": "healpix_index",
                },
                "result": True,
            },
        }
    )

    result = graph.to_callable(process_registry=registry, results_cache={})()

    assert isinstance(result["temperature"].data, dask_array.Array)
    assert result.sizes["healpix_index"] == 2
    np.testing.assert_array_equal(result["healpix_index"].values, [1, 3])
    np.testing.assert_array_equal(result["temperature"].compute().values, [[1, 3]])


def test_dggs_to_raster_process_graph_executes_from_registered_process(monkeypatch):
    dask_array = pytest.importorskip("dask.array")
    registry = _register_test_dedl_processes(monkeypatch, _make_dggs_cube())

    graph = OpenEOProcessGraph(
        pg_data={
            "load": {
                "process_id": "load_stac",
                "arguments": {"url": "memory://healpix.zarr"},
            },
            "raster": {
                "process_id": "dggs_to_raster",
                "arguments": {
                    "data": {"from_node": "load"},
                    "resolution": 0.5,
                    "projection": 4326,
                    "extent": {
                        "west": 10.0,
                        "south": 44.5,
                        "east": 11.0,
                        "north": 45.5,
                    },
                },
                "result": True,
            },
        }
    )

    result = graph.to_callable(process_registry=registry, results_cache={})()

    assert isinstance(result["temperature"].data, dask_array.Array)
    assert result.sizes == {"t": 1, "y": 2, "x": 2}
    assert result.attrs["crs"] == "EPSG:4326"
    np.testing.assert_array_equal(
        result["temperature"].compute().values,
        np.array([[[0, 1], [2, 3]]]),
    )
