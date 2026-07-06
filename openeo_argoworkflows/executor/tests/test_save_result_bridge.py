import importlib.util
import json
from pathlib import Path


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
