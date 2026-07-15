from __future__ import annotations

import sys
import types

from openeo_pg_parser_networkx import ProcessRegistry


def test_register_dedl_cube_load_processes(monkeypatch):
    stactools = types.ModuleType("stactools")
    stactools_core = types.ModuleType("stactools.core")
    stactools_projection = types.ModuleType("stactools.core.projection")
    stactools_core.projection = stactools_projection
    stactools.core = stactools_core
    monkeypatch.setitem(sys.modules, "stactools", stactools)
    monkeypatch.setitem(sys.modules, "stactools.core", stactools_core)
    monkeypatch.setitem(sys.modules, "stactools.core.projection", stactools_projection)

    from openeo_argoworkflows_executor.executor import (
        _register_dedl_cube_load_processes,
    )

    package = types.ModuleType("openeo_processes_dedl_cube_load")
    specs = types.ModuleType("openeo_processes_dedl_cube_load.specs")

    for process_name in (
        "load_stac",
        "filter_dggs",
        "raster_to_dggs",
        "dggs_to_raster",
    ):
        implementation = lambda **_: None
        implementation.__name__ = process_name
        setattr(package, process_name, implementation)
        setattr(specs, process_name, {"id": process_name})

    package.specs = specs
    monkeypatch.setitem(sys.modules, "openeo_processes_dedl_cube_load", package)
    monkeypatch.setitem(sys.modules, "openeo_processes_dedl_cube_load.specs", specs)

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
