import json

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
