import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import rioxarray  # noqa: F401
import xarray as xr

from openeo_argoworkflows_executor.cli import (
    _collect_result_files,
    _find_stac_collections,
    _publish_stac_collection,
)


def test_collect_result_files_recurses_and_skips_directories_and_hidden_files(tmp_path):
    nested = tmp_path / "package-output" / "items"
    nested.mkdir(parents=True)
    data = nested / "result.tif"
    data.write_bytes(b"tif")
    hidden = nested / ".hidden"
    hidden.write_text("skip")

    files = _collect_result_files(str(tmp_path))

    assert files == [str(data)]


def test_find_stac_collections_returns_package_collection_jsons(tmp_path):
    collection = tmp_path / "package-output" / "result.json"
    collection.parent.mkdir()
    collection.write_text(json.dumps({"type": "Collection", "id": "save_result"}))
    feature = tmp_path / "package-output" / "items" / "item.json"
    feature.parent.mkdir()
    feature.write_text(json.dumps({"type": "Feature", "id": "item"}))

    assert _find_stac_collections(str(tmp_path)) == [collection]


def test_publish_stac_collection_normalizes_and_posts_collection_and_items(
    tmp_path, monkeypatch
):
    for var in ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)

    package_dir = tmp_path / "RESULTS" / "package-output"
    package_items = package_dir / "items"
    package_items.mkdir(parents=True)
    collection = package_dir / "save_result.json"
    collection.write_text(
        json.dumps(
            {
                "type": "Collection",
                "id": "save_result",
                "links": [{"rel": "items", "href": "/save_result/items"}],
            }
        )
    )
    item = package_items / "item.json"
    item.write_text(
        json.dumps(
            {
                "type": "Feature",
                "id": "item",
                "assets": {"data": {"href": str(package_dir / "result.tif")}},
            }
        )
    )

    posts = []
    stac_path = tmp_path / "STAC"
    _publish_stac_collection(
        collection_file=collection,
        stac_path=str(stac_path),
        job_id="job-123",
        stac_api_url="https://stac.example/",
        post_json_func=lambda url, payload: posts.append((url, payload)),
    )

    saved_collection = json.loads((stac_path / "job-123.json").read_text())
    assert saved_collection["id"] == "job-123"
    assert (stac_path / "items" / "item.json").exists()
    assert posts == [
        ("https://stac.example/", saved_collection),
        (
            "https://stac.example/job-123/items",
            json.loads((stac_path / "items" / "item.json").read_text()),
        ),
    ]


# --- Integration tests with real package-generated STAC directories ---


def _find_single_collection(directory: Path) -> Path:
    import json
    for f in sorted(directory.rglob("*.json")):
        try:
            payload = json.loads(f.read_text())
        except Exception:
            continue
        if payload.get("type") == "Collection":
            return f
    raise AssertionError(f"No Collection JSON found under {directory}")


def _temporal_dataarray_cube():
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


def _load_io_module():
    spec = importlib.util.spec_from_file_location(
        "io_impl",
        Path(__file__).parent.parent
        / "openeo_argoworkflows_executor/extra_processes/process_implementations/io.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_publish_stac_collection_with_real_package_gtiff_rewrites_hrefs(
    tmp_path, monkeypatch
):
    pytest.importorskip("openeo_processes_save_result.save_result")
    monkeypatch.setenv("OPENEO_RESULTS_PATH", str(tmp_path))
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.example")
    monkeypatch.setenv("S3_BUCKET", "eo-public")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("OPENEO_USER_ID", "u-1")
    monkeypatch.setenv("OPENEO_JOB_ID", "j-1")

    io = _load_io_module()
    result_path = io.save_result(
        _temporal_dataarray_cube(),
        format="GTiff",
        options={"collection_id": "integration-gtiff"},
    )
    result_dir = Path(result_path).parent.parent

    collection_file = _find_single_collection(result_dir)

    posts = []
    stac_dir = tmp_path / "STAC"
    with patch(
        "openeo_argoworkflows_executor.extra_processes.process_implementations.s3.upload_to_s3",
        return_value="s3://eo-public/u-1/j-1/result.tif",
    ):
        _publish_stac_collection(
            collection_file=collection_file,
            stac_path=str(stac_dir),
            job_id="j-1",
            stac_api_url="https://stac.example/",
            post_json_func=lambda url, payload: posts.append((url, payload)),
        )

    for item_file in sorted((stac_dir / "items").glob("*.json")):
        published_item = json.loads(item_file.read_text())
        for asset in published_item["assets"].values():
            assert asset["href"].startswith("s3://"), (
                f"asset href should be S3 URI after publishing, got: {asset['href']}"
            )
    num_items = len(list((stac_dir / "items").glob("*.json")))
    assert len(posts) == 1 + num_items, "expected 1 collection + N items"
    assert posts[0][0] == "https://stac.example/"
    assert "/items" in posts[1][0]


def test_publish_stac_collection_with_real_package_netcdf_rewrites_hrefs(
    tmp_path, monkeypatch
):
    pytest.importorskip("openeo_processes_save_result.save_result")
    monkeypatch.setenv("OPENEO_RESULTS_PATH", str(tmp_path))
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.example")
    monkeypatch.setenv("S3_BUCKET", "eo-public")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("OPENEO_USER_ID", "u-1")
    monkeypatch.setenv("OPENEO_JOB_ID", "j-1")

    io = _load_io_module()
    result_path = io.save_result(
        _temporal_dataarray_cube(),
        format="NetCDF",
        options={"collection_id": "integration-netcdf"},
    )
    result_dir = Path(result_path).parent.parent

    collection_file = _find_single_collection(result_dir)

    posts = []
    stac_dir = tmp_path / "STAC"
    with patch(
        "openeo_argoworkflows_executor.extra_processes.process_implementations.s3.upload_to_s3",
        return_value="s3://eo-public/u-1/j-1/result.nc",
    ):
        _publish_stac_collection(
            collection_file=collection_file,
            stac_path=str(stac_dir),
            job_id="j-1",
            stac_api_url="https://stac.example/",
            post_json_func=lambda url, payload: posts.append((url, payload)),
        )

    for item_file in sorted((stac_dir / "items").glob("*.json")):
        published_item = json.loads(item_file.read_text())
        for asset in published_item["assets"].values():
            assert asset["href"].startswith("s3://"), (
                f"asset href should be S3 URI after publishing, got: {asset['href']}"
            )


def test_publish_stac_collection_with_real_package_zarr_does_not_pollute_directory(
    tmp_path, monkeypatch
):
    pytest.importorskip("openeo_processes_save_result.save_result")
    monkeypatch.setenv("OPENEO_RESULTS_PATH", str(tmp_path))

    io = _load_io_module()
    result_path = io.save_result(
        _temporal_dataarray_cube(),
        format="Zarr",
        options={"collection_id": "integration-zarr"},
    )
    result_dir = Path(result_path)

    assert result_dir.is_dir()
    zarr_contents_before = sorted(result_dir.rglob("*"))

    collection_file = _find_single_collection(result_dir.parent)

    posts = []
    stac_dir = tmp_path / "STAC"
    _publish_stac_collection(
        collection_file=collection_file,
        stac_path=str(stac_dir),
        job_id="j-1",
        stac_api_url="https://stac.example/",
        post_json_func=lambda url, payload: posts.append((url, payload)),
    )

    zarr_contents_after = sorted(result_dir.rglob("*"))
    assert zarr_contents_before == zarr_contents_after, (
        "STAC publishing must not add or remove files inside the Zarr directory"
    )
    published_items = sorted((stac_dir / "items").glob("*.json"))
    assert len(published_items) >= 1, "Expected at least one STAC item to be published"
