"""Tests for _find_stac_root — top-level and Directory-output cases."""

import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

# Import cwl.py directly to avoid __init__.py pulling in heavy deps (odc, gdal)
_cwl_spec = importlib.util.spec_from_file_location(
    "cwl",
    Path(__file__).parent.parent
    / "openeo_argoworkflows_executor/extra_processes/process_implementations/cwl.py",
)
_cwl = importlib.util.module_from_spec(_cwl_spec)
_cwl_spec.loader.exec_module(_cwl)
_find_stac_root = _cwl._find_stac_root


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data))


class TestFindStacRoot:

    def test_finds_catalog_at_top_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            _write_json(d / "catalog.json", {"type": "Catalog"})
            result = _find_stac_root(d)
            assert result == d / "catalog.json"

    def test_finds_collection_at_top_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            _write_json(d / "collection.json", {"type": "Collection"})
            result = _find_stac_root(d)
            assert result == d / "collection.json"

    def test_finds_catalogue_spelling_at_top_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            _write_json(d / "catalogue.json", {"type": "Catalog"})
            result = _find_stac_root(d)
            assert result == d / "catalogue.json"

    def test_returns_none_when_no_stac_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "output.tif").write_bytes(b"fake tiff")
            result = _find_stac_root(d)
            assert result is None

    def test_returns_none_for_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert _find_stac_root(Path(tmpdir)) is None

    def test_finds_collection_inside_subdirectory(self):
        """Directory-type CWL output — collection.json is one level down."""
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            subdir = d / "output_results"
            subdir.mkdir()
            _write_json(subdir / "collection.json", {"type": "Collection"})
            result = _find_stac_root(d)
            assert result == subdir / "collection.json"

    def test_top_level_takes_priority_over_subdirectory(self):
        """If collection.json exists at both levels, top level wins."""
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            _write_json(d / "catalog.json", {"type": "Catalog", "level": "top"})
            subdir = d / "subdir"
            subdir.mkdir()
            _write_json(subdir / "catalog.json", {"type": "Catalog", "level": "sub"})
            result = _find_stac_root(d)
            data = json.loads(result.read_text())
            assert data["level"] == "top"

    def test_ignores_non_stac_json_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            _write_json(d / "item.json", {"type": "Feature"})
            _write_json(d / "config.json", {"setting": "value"})
            assert _find_stac_root(d) is None

    def test_finds_stac_in_one_of_multiple_subdirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "tmp").mkdir()
            output = d / "output"
            output.mkdir()
            _write_json(output / "collection.json", {"type": "Collection"})
            result = _find_stac_root(d)
            assert result == output / "collection.json"
