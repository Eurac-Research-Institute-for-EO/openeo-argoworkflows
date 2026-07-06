import importlib.util
import json
import sys
import types
from pathlib import Path

import xarray as xr


_io_spec = importlib.util.spec_from_file_location(
    "io_impl",
    Path(__file__).parent.parent
    / "openeo_argoworkflows_executor/extra_processes/process_implementations/io.py",
)
_io = importlib.util.module_from_spec(_io_spec)
_io_spec.loader.exec_module(_io)


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
