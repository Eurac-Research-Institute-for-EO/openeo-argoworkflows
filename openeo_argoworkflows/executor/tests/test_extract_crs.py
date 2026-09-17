"""
TDD tests for _extract_crs(item) — standalone CRS extraction from a STAC item.

Priority order: proj:wkt2 > proj:epsg > proj:code > default EPSG:4326
"""
import importlib.util
import pathlib
import pytest
from unittest.mock import MagicMock


def _load_crs_module():
    spec = importlib.util.spec_from_file_location(
        "crs",
        pathlib.Path(__file__).parent.parent
        / "openeo_argoworkflows_executor"
        / "crs.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_item(properties: dict) -> MagicMock:
    item = MagicMock()
    item.properties = properties
    return item


# --- proj:wkt2 present ---
def test_extract_crs_from_wkt2():
    import pyproj
    mod = _load_crs_module()
    wkt = pyproj.CRS.from_epsg(3035).to_wkt()
    item = _make_item({"proj:wkt2": wkt})
    crs = mod._extract_crs(item)
    assert crs.to_epsg() == 3035


# --- proj:epsg present ---
def test_extract_crs_from_epsg():
    mod = _load_crs_module()
    item = _make_item({"proj:epsg": 32632})
    crs = mod._extract_crs(item)
    assert crs.to_epsg() == 32632


# --- proj:code present (proj extension v2) ---
def test_extract_crs_from_proj_code():
    mod = _load_crs_module()
    item = _make_item({"proj:code": "EPSG:3035"})
    crs = mod._extract_crs(item)
    assert crs.to_epsg() == 3035


# --- no CRS fields --- default to EPSG:4326 ---
def test_extract_crs_defaults_to_4326():
    mod = _load_crs_module()
    item = _make_item({})
    crs = mod._extract_crs(item)
    assert crs.to_epsg() == 4326


# --- priority: wkt2 wins over epsg ---
def test_extract_crs_wkt2_takes_priority_over_epsg():
    import pyproj
    mod = _load_crs_module()
    wkt = pyproj.CRS.from_epsg(3035).to_wkt()
    item = _make_item({"proj:wkt2": wkt, "proj:epsg": 32632})
    crs = mod._extract_crs(item)
    assert crs.to_epsg() == 3035


# --- priority: epsg wins over proj:code ---
def test_extract_crs_epsg_takes_priority_over_proj_code():
    mod = _load_crs_module()
    item = _make_item({"proj:epsg": 32632, "proj:code": "EPSG:3035"})
    crs = mod._extract_crs(item)
    assert crs.to_epsg() == 32632
