import logging
import os
import uuid
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pyproj
import pystac_client
import xarray as xr
from odc.stac import stac_load
from openeo_pg_parser_networkx.pg_schema import BoundingBox, GeoJson, TemporalInterval
from openeo_processes_dask.process_implementations.cubes._filter import filter_bbox
from openeo_processes_dask.process_implementations.data_model import RasterCube
from pystac.extensions import raster

from openeo_argoworkflows_executor.crs import _extract_crs
from openeo_argoworkflows_executor.timeout import compute_with_timeout

__all__ = ["load_collection", "save_result"]

logger = logging.getLogger(__name__)

_PACKAGE_SAVE_RESULT_FORMATS = {"GTIFF", "COG", "NETCDF", "ZARR"}


def load_collection(
    id: str,
    spatial_extent: Optional[Union[BoundingBox, dict, str, GeoJson]] = None,
    temporal_extent: Optional[TemporalInterval] = None,
    bands: Optional[list[str]] = None,
    properties: Optional[dict] = None,
    **kwargs,
):
    """Load a collection from the STAC API.

    This implementation fixes the variable initialization bug and adds
    better error handling and logging.
    """
    query_dict = {}

    query_dict["collections"] = [id]

    if spatial_extent is None:
        raise Exception(
            "No spatial extent was provided, will not load the entire x and y axis of the datacube."
        )
    elif temporal_extent is None:
        raise Exception(
            "No temporal extent was provided, will not load the entire temporal axis of the datacube."
        )

    if isinstance(spatial_extent, BoundingBox):
        query_dict["bbox"] = (
            spatial_extent.west,
            spatial_extent.south,
            spatial_extent.east,
            spatial_extent.north,
        )
    else:
        raise ValueError("Provided spatial extent could not be interpreted.")

    # Format datetime properly for STAC API (needs RFC 3339 / ISO 8601)
    datetime_parts = []
    for time in temporal_extent:
        if time != "None" and time is not None:
            val = time.root if hasattr(time, "root") else time
            from datetime import datetime as dt

            if isinstance(val, dt):
                time_str = val.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                time_str = str(val).strip()
                # Convert Python datetime string format to ISO 8601
                if " " in time_str and "T" not in time_str:
                    # "2020-01-01 00:00:00+00:00" → take date part only
                    time_str = time_str.split(" ")[0] + "T00:00:00Z"
                elif "T" not in time_str:
                    time_str = f"{time_str}T00:00:00Z"
            datetime_parts.append(time_str)

    query_dict["datetime"] = "/".join(datetime_parts) if datetime_parts else None
    logger.info(f"Formatted datetime: {query_dict['datetime']}")

    if "STAC_API_URL" not in os.environ:
        raise Exception("STAC URL Not available in executor config.")

    logger.info(f"Connecting to STAC API: {os.environ['STAC_API_URL']}")
    logger.info(f"Query parameters: {query_dict}")

    catalog = pystac_client.Client.open(os.environ["STAC_API_URL"])
    results = catalog.search(**query_dict, limit=100)

    result_items = list(results.items())

    if not result_items:
        bbox = query_dict.get("bbox", "N/A")
        datetime_range = query_dict.get("datetime", "N/A")
        error_msg = (
            f"No data found for collection '{id}' with the given parameters:\n"
            f"  - Bounding box: {bbox}\n"
            f"  - Time range: {datetime_range}\n"
            f"Please verify that data exists for this location and time period."
        )
        logger.error(error_msg)
        raise Exception(error_msg)

    logger.info(f"Found {len(result_items)} items")

    example_item = result_items[0]

    crs = _extract_crs(example_item)

    # Initialize variables with defaults
    resolution = None
    nodata = None
    dtype = None

    # Try to extract raster metadata from item
    if raster.RasterExtension.has_extension(example_item):
        for asset in example_item.get_assets().values():
            if "raster:bands" in asset.extra_fields.keys():
                for band in asset.extra_fields["raster:bands"]:
                    if "spatial_resolution" in band and resolution is None:
                        resolution = band["spatial_resolution"]
                    if "nodata" in band and nodata is None:
                        nodata = band["nodata"]
                    if "data_type" in band and dtype is None:
                        dtype = band["data_type"]
            if resolution and nodata and dtype:
                break

    # If resolution not found, determine from CRS
    if resolution is None:
        crs_measurement = crs.axis_info[0].unit_name if crs.axis_info else "metre"

        if crs_measurement == "metre":
            resolution = 10  # Default 10m for metric CRS
        elif crs_measurement == "degree":
            resolution = 0.0001  # ~10m at equator
        else:
            resolution = 10  # Default fallback

        logger.info(
            f"Resolution not found in metadata, using default: {resolution} ({crs_measurement})"
        )
    else:
        logger.info(f"Using resolution from metadata: {resolution}")

    # Build kwargs for stac_load
    load_kwargs = {}

    if dtype:
        load_kwargs["dtype"] = dtype
        # Ensure nodata matches dtype
        if "int" in dtype and isinstance(nodata, float):
            nodata = int(nodata)

    if nodata is not None:
        load_kwargs["nodata"] = nodata

    # Filter to only load "data" asset (exclude thumbnails, tilejson, etc.)
    # Check what assets are available
    if result_items:
        available_assets = list(result_items[0].assets.keys())
        logger.info(f"Available assets: {available_assets}")

        # Use user-specified bands if provided, otherwise fall back to asset detection
        if bands is not None:
            load_kwargs["bands"] = bands
            logger.info(f"Loading user-specified bands: {bands}")
        else:
            # Known non-data assets to always exclude (thumbnails, previews, metadata)
            non_data_assets = {
                "thumbnail",
                "tilejson",
                "preview",
                "metadata",
                "visual",
                "rendered_preview",
                "info",
            }

            # Select only known data assets if present
            known_data_assets = {
                "data",
                "B01",
                "B02",
                "B03",
                "B04",
                "B05",
                "B06",
                "B07",
                "B08",
                "B8A",
                "B09",
                "B10",
                "B11",
                "B12",
            }
            data_assets = [
                a for a in available_assets if a in known_data_assets
            ]
            if not data_assets:
                # Fallback: exclude known non-data assets, keep everything else
                data_assets = [
                    a
                    for a in available_assets
                    if a not in non_data_assets
                ]
            if data_assets:
                load_kwargs["bands"] = data_assets
                logger.info(f"Loading auto-detected bands/assets: {data_assets}")

    # odc-stac only understands proj:epsg/proj:wkt2/proj:projjson at the asset level.
    # Normalize proj:code (proj extension v2) → proj:epsg so odc-stac can parse the items.
    for item in result_items:
        for asset in item.assets.values():
            code = asset.extra_fields.get("proj:code", "")
            if code.startswith("EPSG:") and "proj:epsg" not in asset.extra_fields:
                try:
                    asset.extra_fields["proj:epsg"] = int(code.split(":")[1])
                except ValueError:
                    pass

    logger.info(
        f"Loading data with CRS={crs}, resolution={resolution}, kwargs={load_kwargs}"
    )

    lazy_xarray = stac_load(
        result_items,
        crs=crs,
        resolution=resolution,
        chunks={"x": 2048, "y": 2048},
        **load_kwargs,
    ).to_array(dim="bands")

    logger.info(
        f"Loaded xarray with shape: {lazy_xarray.shape}, dims: {lazy_xarray.dims}"
    )

    # Clip to the original bounding box
    return filter_bbox(lazy_xarray, extent=spatial_extent)


def save_result(
    data: RasterCube,
    format: str = "netcdf",
    options: Optional[dict] = None,
):
    """Save the result data cube to a file."""
    options = dict(options or {})
    fmt_upper = format.upper()

    use_package_writer = options.pop("use_package_save_result", False)
    if fmt_upper in _PACKAGE_SAVE_RESULT_FORMATS or use_package_writer:
        return _save_result_with_process_package(data, fmt_upper, options)

    supported = ", ".join(sorted(_PACKAGE_SAVE_RESULT_FORMATS))
    raise ValueError(
        f"Data can't be transformed into the requested output format '{format}'. "
        f"Supported formats: {supported}"
    )


def _save_result_with_process_package(
    data: RasterCube,
    fmt_upper: str,
    options: dict,
) -> str:
    """Delegate richer output formats to openeo-processes-save-result.

    The standalone process returns STAC metadata. In argoworkflows, downstream
    EOAP-CWL staging expects a local path, so this wrapper returns the first
    local asset path referenced by that STAC output, falling back to the
    collection JSON or output folder.
    """
    try:
        from openeo_processes_save_result.save_result import (
            save_result as package_save_result,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Output format "
            f"'{fmt_upper}' requires openeo-processes-save-result to be installed "
            "in the executor image."
        ) from exc

    results_path = Path(os.environ.get("OPENEO_RESULTS_PATH", "/tmp/results"))
    results_path.mkdir(parents=True, exist_ok=True)
    output_folder = Path(
        options.setdefault("output_folder", str(results_path / str(uuid.uuid4())))
    )
    collection_id = options.get("collection_id", "save_result")

    cube = _as_dataset_for_save_result_package(data)

    # Executor pods run in air-gapped environments where PySTAC cannot fetch
    # remote STAC extension schemas (stac-extensions.github.io). Disable
    # validation to prevent GetSchemaError in offline mode.
    options = dict(options)
    options.setdefault("skip_validation", True)

    stac = package_save_result(data=cube, format=fmt_upper, options=options)

    staged_path = _local_asset_path_from_stac(stac, output_folder)
    if staged_path is not None:
        logger.info(
            "Successfully saved result via openeo-processes-save-result: %s",
            staged_path,
        )
        return str(staged_path)

    if fmt_upper == "ZARR" and output_folder.exists():
        return str(output_folder)

    collection_json = output_folder / f"{collection_id}.json"
    if collection_json.exists():
        return str(collection_json)

    return str(output_folder)


def _as_dataset_for_save_result_package(data: RasterCube) -> xr.Dataset:
    if isinstance(data, xr.Dataset):
        return data

    dim = data.openeo.band_dims[0] if data.openeo.band_dims else None
    return data.to_dataset(
        dim=dim, name="name" if not dim else None, promote_attrs=True
    )


def _local_asset_path_from_stac(stac: dict, output_folder: Path) -> Optional[Path]:
    asset_refs = []

    if stac.get("type") == "Feature":
        asset_refs.extend(
            (asset.get("href"), output_folder)
            for asset in stac.get("assets", {}).values()
        )

    for link in stac.get("links", []):
        if link.get("rel") != "item":
            continue
        href = link.get("href")
        if not href:
            continue
        item_path = _resolve_local_href(href, output_folder)
        if item_path is None or not item_path.exists() or item_path.suffix != ".json":
            continue
        try:
            import json

            with open(item_path) as f:
                item = json.load(f)
        except Exception as exc:
            logger.warning("Could not read STAC item %s: %s", item_path, exc)
            continue
        asset_refs.extend(
            (asset.get("href"), item_path.parent)
            for asset in item.get("assets", {}).values()
        )

    items_dir = output_folder / "items"
    if items_dir.exists():
        for item_path in sorted(items_dir.glob("*.json")):
            try:
                import json

                with open(item_path) as f:
                    item = json.load(f)
            except Exception as exc:
                logger.warning("Could not read STAC item %s: %s", item_path, exc)
                continue
            asset_refs.extend(
                (asset.get("href"), item_path.parent)
                for asset in item.get("assets", {}).values()
            )

    for href, base in asset_refs:
        path = _resolve_local_href(href, base)
        if path is not None and path.exists():
            return path

    return None


def _resolve_local_href(href: Optional[str], base: Path) -> Optional[Path]:
    if not href or "://" in href:
        return None

    path = Path(href)
    if not path.is_absolute():
        path = base / href.lstrip("./")
    return path
