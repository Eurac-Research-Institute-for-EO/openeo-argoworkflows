"""Tests for CWL process detection and STAC generation."""

import json
import os
import tempfile

from openeo_argoworkflows_executor.executor import _is_cwl_job
from openeo_argoworkflows_executor.stac_cwl import _get_media_type, create_cwl_stac


class TestIsCwlJob:
    """Tests for _is_cwl_job detection logic."""

    def test_detects_run_cwl_process(self):
        pg = {
            "run1": {
                "process_id": "run_cwl",
                "arguments": {
                    "cwl": "https://example.com/workflow.cwl",
                    "inputs": {"message": "hello"},
                },
                "result": True,
            }
        }
        assert _is_cwl_job(pg) is True

    def test_rejects_standard_process_graph(self):
        pg = {
            "load1": {
                "process_id": "load_collection",
                "arguments": {"id": "S2", "spatial_extent": {}},
            },
            "save1": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "load1"}, "format": "netcdf"},
                "result": True,
            },
        }
        assert _is_cwl_job(pg) is False

    def test_rejects_empty_process_graph(self):
        assert _is_cwl_job({}) is False

    def test_detects_cwl_in_mixed_graph(self):
        pg = {
            "load1": {
                "process_id": "load_collection",
                "arguments": {"id": "S2"},
            },
            "cwl1": {
                "process_id": "run_cwl",
                "arguments": {"cwl": "inline cwl", "inputs": {}},
                "result": True,
            },
        }
        assert _is_cwl_job(pg) is True


class TestGetMediaType:
    """Tests for media type detection."""

    def test_geotiff(self):
        assert "geotiff" in _get_media_type("output.tif")

    def test_netcdf(self):
        assert _get_media_type("data.nc") == "application/x-netcdf"

    def test_json(self):
        assert _get_media_type("result.json") == "application/json"

    def test_csv(self):
        assert _get_media_type("table.csv") == "text/csv"

    def test_unknown(self):
        assert _get_media_type("data.xyz123") == "application/octet-stream"


class TestCreateCwlStac:
    """Tests for CWL STAC generation."""

    def test_creates_collection_and_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake result files
            results_dir = os.path.join(tmpdir, "results")
            os.makedirs(results_dir)
            file1 = os.path.join(results_dir, "output.txt")
            file2 = os.path.join(results_dir, "data.json")
            with open(file1, "w") as f:
                f.write("hello")
            with open(file2, "w") as f:
                json.dump({"key": "value"}, f)

            stac_path = os.path.join(tmpdir, "stac")

            # Don't actually POST to STAC API
            create_cwl_stac(
                job_id="test-job-123",
                result_files=[file1, file2],
                stac_path=stac_path,
                stac_api_url="http://localhost:9999/",
            )

            # Check collection was created
            collection_file = os.path.join(stac_path, "test-job-123.json")
            assert os.path.exists(collection_file)
            with open(collection_file) as f:
                collection = json.load(f)
            assert collection["id"] == "test-job-123"
            assert collection["type"] == "Collection"

            # Check items were created
            items_file = os.path.join(stac_path, "inline_items.csv")
            assert os.path.exists(items_file)
            with open(items_file) as f:
                lines = [line.strip() for line in f if line.strip()]
            assert len(lines) == 2

            item1 = json.loads(lines[0])
            assert item1["type"] == "Feature"
            assert item1["collection"] == "test-job-123"
            assert "output.txt" in item1["assets"]
