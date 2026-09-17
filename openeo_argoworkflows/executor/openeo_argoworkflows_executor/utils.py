from types import SimpleNamespace

from openeo_pg_parser_networkx import OpenEOProcessGraph

_BBOX_KEYS = ("bbox", "spatial_extent")


def _bbox_from_dict(d: dict):
    """Return a SimpleNamespace with .west/.south/.east/.north from a dict, or None."""
    if isinstance(d, dict) and all(k in d for k in ("west", "south", "east", "north")):
        return SimpleNamespace(west=d["west"], south=d["south"], east=d["east"], north=d["north"])
    return None


def get_pg_bounding_box(process_graph: dict):
    graph = OpenEOProcessGraph(pg_data=process_graph)

    load_calls = [
        value for key, value in graph.nodes if "load_" in value["process_id"]
    ]
    for call in load_calls:
        if "spatial_extent" in call["resolved_kwargs"]:
            return call["resolved_kwargs"]["spatial_extent"]

    # Fallback: process graph is a UDP call — bbox is passed as a top-level argument
    for node in process_graph.values():
        if not isinstance(node, dict):
            continue
        for key in _BBOX_KEYS:
            bbox = _bbox_from_dict(node.get("arguments", {}).get(key))
            if bbox:
                return bbox

    return None


def derive_sub_graph(cell, process_graph: dict):

    west, south, east, north = cell[2].bounds
    tile_bbox = {"west": west, "east": east, "south": south, "north": north}

    # Standard case: load_collection at the top level
    for key, value in process_graph.items():
        if not isinstance(value, dict):
            continue
        if "load_" in value.get("process_id", ""):
            process_graph[key]["arguments"]["spatial_extent"] = tile_bbox
            return process_graph

    # Fallback: UDP call — update the bbox/spatial_extent argument directly
    for key, value in process_graph.items():
        if not isinstance(value, dict):
            continue
        args = value.get("arguments", {})
        for bbox_key in _BBOX_KEYS:
            if isinstance(args.get(bbox_key), dict):
                process_graph[key]["arguments"][bbox_key] = tile_bbox
                return process_graph

    return process_graph