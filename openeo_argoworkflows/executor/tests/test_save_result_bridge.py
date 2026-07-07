import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
import rioxarray  # noqa: F401  registers .rio accessor
import xarray as xr


_io_spec = importlib.util.spec_from_file_location(
    "io_impl",
    Path(__file__).parent.parent
    / "openeo_argoworkflows_executor/extra_processes/process_implementations/io.py",
)
_io = importlib.util.module_from_spec(_io_spec)
_io_spec.loader.exec_module(_io)

_cwl_spec = importlib.util.spec_from_file_location(
    "cwl",
    Path(__file__).parent.parent
    / "openeo_argoworkflows_executor/extra_processes/process_implementations/cwl.py",
)
_cwl = importlib.util.module_from_spec(_cwl_spec)
_cwl_spec.loader.exec_module(_cwl)


def _temporal_dataarray_cube() -> xr.DataArray:
    data = xr.DataArray(
        np.ones((2, 3, 4, 2), dtype="float32"),
        dims=("bands", "y", "x", "t"),
        coords={
            "bands": ["B01", "B02"],
            "y": np.arange(3).astype(float),
            "x": np.arange(4).astype(float),
            "t": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        },
        attrs={
            "openeo_x_dim": "x",
            "openeo_y_dim": "y",
            "openeo_temporal_dims": ["t"],
            "openeo_band_dims": ["bands"],
        },
        name="cube",
    )
    return data.rio.write_crs("EPSG:4326")


def test_local_asset_path_from_stac_resolves_item_asset_relative_to_item(tmp_path):
    items_dir = tmp_path / "items"
    items_dir.mkdir()
    asset = items_dir / "result.tif"
    asset.write_bytes(b"tif")
    item = items_dir / "item.json"
    item.write_text(
        json.dumps({"type": "Feature", "assets": {"data": {"href": "result.tif"}}})
    )

    collection = {
        "type": "Collection",
        "links": [{"rel": "item", "href": "items/item.json"}],
    }

    assert _io._local_asset_path_from_stac(collection, tmp_path) == asset


def test_resolve_local_href_ignores_remote_href(tmp_path):
    assert _io._resolve_local_href("s3://bucket/result.tif", tmp_path) is None


def test_package_save_result_fallback_keeps_collection_id(monkeypatch, tmp_path):
    package = types.ModuleType("openeo_processes_save_result")
    save_result_module = types.ModuleType("openeo_processes_save_result.save_result")

    def fake_save_result(data, format, options):
        collection_id = options.pop("collection_id")
        options.pop("output_folder")
        (tmp_path / f"{collection_id}.json").write_text("{}")
        return {}

    save_result_module.save_result = fake_save_result
    monkeypatch.setitem(sys.modules, "openeo_processes_save_result", package)
    monkeypatch.setitem(
        sys.modules, "openeo_processes_save_result.save_result", save_result_module
    )

    data = xr.Dataset({"B01": (["y", "x"], [[1]])})
    result = _io._save_result_with_process_package(
        data,
        "GTIFF",
        {"output_folder": str(tmp_path), "collection_id": "custom-result"},
    )

    assert result == str(tmp_path / "custom-result.json")


def test_real_package_bridge_gtiff_temporal_cube_returns_existing_path(
    monkeypatch, tmp_path
):
    pytest.importorskip("openeo_processes_save_result.save_result")
    monkeypatch.setenv("OPENEO_RESULTS_PATH", str(tmp_path))

    result = _io.save_result(
        _temporal_dataarray_cube(),
        format="GTiff",
        options={"collection_id": "argoworkflows-gtiff"},
    )

    assert Path(result).exists()


def test_real_package_bridge_netcdf_temporal_cube_returns_existing_asset(
    monkeypatch, tmp_path
):
    pytest.importorskip("openeo_processes_save_result.save_result")
    monkeypatch.setenv("OPENEO_RESULTS_PATH", str(tmp_path))

    result = _io.save_result(
        _temporal_dataarray_cube(),
        format="NetCDF",
        options={"collection_id": "argoworkflows-netcdf"},
    )

    result_path = Path(result)
    assert result_path.exists()
    assert result_path.suffix == ".nc"


def test_real_package_bridge_zarr_stages_directory_for_cwl(monkeypatch, tmp_path):
    pytest.importorskip("openeo_processes_save_result.save_result")
    monkeypatch.setenv("OPENEO_RESULTS_PATH", str(tmp_path))

    result = _io.save_result(
        _temporal_dataarray_cube(),
        format="Zarr",
        options={"collection_id": "argoworkflows-zarr"},
    )

    assert Path(result).is_dir()
    with patch.object(_cwl, "run_cwl", return_value={"status": "completed"}) as run_cwl:
        _cwl.run_udf(data=result, udf="workflow.cwl", runtime="eoap-cwl", context={})

    inputs = run_cwl.call_args.kwargs["inputs"]
    assert inputs["openeo_data"] == {
        "class": "Directory",
        "location": f"file://{result}",
    }
