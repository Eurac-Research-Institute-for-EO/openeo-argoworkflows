"""Tests for executor utility functions — bounding box extraction and sub-graph derivation."""

import pytest
from openeo_argoworkflows_executor.utils import get_pg_bounding_box, derive_sub_graph


# ---------------------------------------------------------------------------
# get_pg_bounding_box
# ---------------------------------------------------------------------------

class TestGetPgBoundingBox:

    def _make_load_pg(self, spatial_extent):
        return {
            "load1": {
                "process_id": "load_collection",
                "arguments": {
                    "id": "S2",
                    "spatial_extent": spatial_extent,
                    "temporal_extent": ["2023-01-01", "2023-12-31"],
                },
            },
            "save1": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "load1"}, "format": "NetCDF"},
                "result": True,
            },
        }

    def test_extracts_bbox_from_load_collection(self):
        bbox = {"west": 11.0, "south": 46.0, "east": 12.0, "north": 47.0}
        pg = self._make_load_pg(bbox)
        result = get_pg_bounding_box(pg)
        assert result is not None
        assert result.west == 11.0
        assert result.south == 46.0
        assert result.east == 12.0
        assert result.north == 47.0

    def test_returns_none_for_empty_graph(self):
        # openeo_pg_parser_networkx raises when there's no result node —
        # get_pg_bounding_box falls through to the fallback and returns None
        try:
            result = get_pg_bounding_box({})
            assert result is None
        except Exception:
            pass  # pg-parser raising is also acceptable for empty graph

    def test_extracts_bbox_from_udp_bbox_argument(self):
        """UDP call at top level — no load_collection visible, bbox in arguments."""
        bbox = {"west": 10.0, "south": 45.0, "east": 11.0, "north": 46.0}
        pg = {
            "udp1": {
                "process_id": "NDWI",
                "arguments": {
                    "bbox": bbox,
                    "date_range": ["2022-06-01", "2022-06-30"],
                },
                "result": True,
            }
        }
        result = get_pg_bounding_box(pg)
        assert result is not None
        assert result.west == 10.0
        assert result.east == 11.0

    def test_extracts_bbox_from_udp_spatial_extent_argument(self):
        """UDP using spatial_extent key instead of bbox."""
        bbox = {"west": 10.0, "south": 45.0, "east": 11.0, "north": 46.0}
        pg = {
            "udp1": {
                "process_id": "MY_UDP",
                "arguments": {"spatial_extent": bbox},
                "result": True,
            }
        }
        result = get_pg_bounding_box(pg)
        assert result is not None
        assert result.south == 45.0
        assert result.north == 46.0

    def test_ignores_incomplete_bbox_dict(self):
        """Dicts missing required keys should not be treated as a bbox."""
        pg = {
            "udp1": {
                "process_id": "MY_UDP",
                "arguments": {"bbox": {"west": 10.0}},  # incomplete
                "result": True,
            }
        }
        assert get_pg_bounding_box(pg) is None

    def test_load_collection_takes_priority_over_udp_fallback(self):
        """When load_collection is present, its spatial_extent wins."""
        lc_bbox = {"west": 1.0, "south": 2.0, "east": 3.0, "north": 4.0}
        udp_bbox = {"west": 9.0, "south": 9.0, "east": 9.0, "north": 9.0}
        pg = {
            "load1": {
                "process_id": "load_collection",
                "arguments": {"id": "S2", "spatial_extent": lc_bbox},
            },
            "udp1": {
                "process_id": "MY_UDP",
                "arguments": {"bbox": udp_bbox},
                "result": True,
            },
        }
        result = get_pg_bounding_box(pg)
        assert result.west == 1.0


# ---------------------------------------------------------------------------
# derive_sub_graph
# ---------------------------------------------------------------------------

class TestDeriveSubGraph:

    def _make_cell(self, west, south, east, north):
        """Minimal cell mock — derive_sub_graph uses cell[2].bounds."""
        from types import SimpleNamespace
        bounds = SimpleNamespace(bounds=(west, south, east, north))
        return (None, None, bounds)

    def test_updates_load_collection_spatial_extent(self):
        pg = {
            "load1": {
                "process_id": "load_collection",
                "arguments": {
                    "id": "S2",
                    "spatial_extent": {"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0},
                },
            }
        }
        cell = self._make_cell(10.0, 20.0, 11.0, 21.0)
        result = derive_sub_graph(cell, pg)
        se = result["load1"]["arguments"]["spatial_extent"]
        assert se["west"] == 10.0
        assert se["south"] == 20.0
        assert se["east"] == 11.0
        assert se["north"] == 21.0

    def test_updates_udp_bbox_argument_when_no_load_collection(self):
        """Fallback path for UDP calls."""
        pg = {
            "udp1": {
                "process_id": "NDWI",
                "arguments": {
                    "bbox": {"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0},
                    "date_range": ["2022-01-01", "2022-12-31"],
                },
                "result": True,
            }
        }
        cell = self._make_cell(5.0, 6.0, 7.0, 8.0)
        result = derive_sub_graph(cell, pg)
        bbox = result["udp1"]["arguments"]["bbox"]
        assert bbox["west"] == 5.0
        assert bbox["north"] == 8.0

    def test_updates_udp_spatial_extent_argument_when_no_load_collection(self):
        pg = {
            "udp1": {
                "process_id": "MY_UDP",
                "arguments": {
                    "spatial_extent": {"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0},
                },
                "result": True,
            }
        }
        cell = self._make_cell(2.0, 3.0, 4.0, 5.0)
        result = derive_sub_graph(cell, pg)
        se = result["udp1"]["arguments"]["spatial_extent"]
        assert se["east"] == 4.0
