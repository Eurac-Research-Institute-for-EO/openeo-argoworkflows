import logging
import os
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

__all__ = ["load_collection", "save_result"]

logger = logging.getLogger(__name__)


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

    # Extract CRS from item properties
    crs = None
    if "proj:wkt2" in example_item.properties.keys():
        crs = pyproj.CRS.from_wkt(example_item.properties["proj:wkt2"])
        logger.info(f"Using CRS from proj:wkt2: {crs.name}")
    elif "proj:epsg" in example_item.properties.keys():
        crs = pyproj.CRS.from_epsg(example_item.properties["proj:epsg"])
        logger.info(f"Using CRS from proj:epsg: {example_item.properties['proj:epsg']}")
    else:
        # Default to EPSG:4326 if no CRS found
        crs = pyproj.CRS.from_epsg(4326)
        logger.warning("No CRS found in item properties, defaulting to EPSG:4326")

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

    def clean_unused_coordinates(ds):
        """
        Remove all coordinates that are not used in the DataArray dimensions.
        Preserve spatial_ref / crs_wkt which are CF grid_mapping variables.
        """
        used_dims = set()
        for var in ds.dims:
            used_dims.update(ds[var].dims)

        # Always keep CF grid_mapping coordinates so QGIS/GDAL can read the CRS
        keep = {"spatial_ref", "crs_wkt"}

        for coord in list(ds.coords):
            if coord not in used_dims and coord not in keep:
                ds = ds.drop_vars(coord)
        return ds

    import uuid

    logger.info(
        f"Saving result data with shape: {data.shape if hasattr(data, 'shape') else 'unknown'}"
    )
    logger.info(f"Data attrs: {data.attrs}")

    _id = str(uuid.uuid4())

    # Get the results path from environment
    results_path = os.environ.get("OPENEO_RESULTS_PATH", "/tmp/results")
    os.makedirs(results_path, exist_ok=True)

    destination = Path(results_path) / f"{_id}.nc"

    dim = data.openeo.band_dims[0] if data.openeo.band_dims else None

    # Get CRS from rio accessor (rioxarray)
    import rioxarray  # noqa - needed for .rio accessor
    from pyproj import CRS as PyprojCRS

    crs = None
    if hasattr(data, "rio") and data.rio.crs is not None:
        crs = data.rio.crs
        logger.info(f"Got CRS from rio accessor: {crs}")
    else:
        # rioxarray couldn't detect CRS - check coordinate attrs (stackstac stores it there)
        for coord_name in ["x", "y"]:
            if coord_name in data.coords:
                crs_str = data.coords[coord_name].attrs.get("crs")
                if crs_str:
                    try:
                        crs = PyprojCRS.from_user_input(crs_str)
                        logger.info(
                            f"Got CRS from coord '{coord_name}' attr: {crs_str}"
                        )
                        break
                    except Exception as e:
                        logger.warning(f"Could not parse CRS from coord attr: {e}")

    if crs is None and "crs" in data.attrs:
        try:
            crs = PyprojCRS.from_user_input(data.attrs["crs"])
            logger.info(f"Got CRS from data attrs: {data.attrs['crs']}")
        except Exception as e:
            logger.warning(f"Could not parse CRS from data attrs: {e}")

    # Ensure CRS is a proper pyproj CRS object if it came as string
    if isinstance(crs, str):
        try:
            crs = PyprojCRS.from_user_input(crs)
        except Exception as e:
            logger.warning(f"Could not parse CRS string: {e}")
            crs = None

    # Write CRS to the data using rioxarray before converting to dataset
    if crs is not None:
        # write_crs embeds a proper spatial_ref grid_mapping variable (CF convention)
        # which QGIS and rioxarray can read back correctly
        data = data.rio.write_crs(crs)
        logger.info(f"Wrote CRS to data: {data.rio.crs}")

    out_data: xr.Dataset = data.to_dataset(
        dim=dim, name="name" if not dim else None, promote_attrs=True
    )

    dtype = "float32"
    comp = dict(zlib=True, complevel=5, dtype=dtype)

    encoding = {var: comp for var in out_data.data_vars}
    out_data = clean_unused_coordinates(out_data)

    # Write CRS after cleaning so spatial_ref is not stripped
    if crs is not None:
        from pyproj import CRS as PyprojCRS

        crs_obj = crs if isinstance(crs, PyprojCRS) else PyprojCRS.from_user_input(crs)
        crs_wkt = crs_obj.to_wkt()
        epsg = crs_obj.to_epsg()

        # Add spatial_ref as a scalar coordinate (CF convention grid_mapping variable)
        # GDAL NetCDF driver reads crs_wkt + grid_mapping attr on variables
        import numpy as np

        # Get CF grid_mapping_name from pyproj
        cf_params = crs_obj.to_cf()
        cf_grid_mapping_name = cf_params.get("grid_mapping_name", "latitude_longitude")

        spatial_ref_attrs = {
            "crs_wkt": crs_wkt,
            "spatial_ref": crs_wkt,  # GDAL also checks this key
            "grid_mapping_name": cf_grid_mapping_name,
        }
        # Add all CF parameters so GDAL can fully reconstruct the CRS
        spatial_ref_attrs.update(cf_params)

        if epsg:
            spatial_ref_attrs["EPSG"] = epsg

        logger.info(
            f"CRS prepared (CF: {cf_grid_mapping_name}, EPSG: {epsg}) — will write via netCDF4"
        )

    # Add standard_name to x/y so GDAL recognises them as projected axes
    # even before reading spatial_ref
    if crs is not None:
        if "x" in out_data.coords:
            out_data["x"].attrs["standard_name"] = "projection_x_coordinate"
            out_data["x"].attrs["long_name"] = "x coordinate of projection"
        if "y" in out_data.coords:
            out_data["y"].attrs["standard_name"] = "projection_y_coordinate"
            out_data["y"].attrs["long_name"] = "y coordinate of projection"

    # Remove attrs that are not netCDF-serializable (dicts, objects, etc.)
    # e.g. reduced_dimensions_min_values is a dict set by reduce_dimension
    valid_types = (str, int, float, bytes, list, tuple, np.ndarray, np.generic)
    for key in list(out_data.attrs):
        if not isinstance(out_data.attrs[key], valid_types):
            logger.debug(f"Dropping non-serializable attr '{key}': {type(out_data.attrs[key])}")
            del out_data.attrs[key]

    logger.info(f"Writing netCDF to: {destination}")
    # Restore reduced temporal dimensions so raster2stac can process it
    reduced_dims = out_data.attrs.get("reduced_dimensions_min_values",  {})
    for dim_name, min_val in reduced_dims.items():
        if dim_name not in out_data.dims:
            out_data = out_data.expand_dims({dim_name: [min_val]})
    out_data.to_netcdf(path=destination, encoding=encoding)

    # Re-open with netCDF4 to fix spatial_ref: xarray writes scalar coords as
    # data variables with a dimension, but GDAL needs a true dimensionless variable
    if crs is not None:
        import netCDF4

        with netCDF4.Dataset(destination, "a") as nc:
            # Remove xarray's broken spatial_ref if present, recreate properly
            if "spatial_ref" in nc.variables:
                # netCDF4 can't delete variables, so just clear and rewrite attrs
                sr = nc.variables["spatial_ref"]
            else:
                sr = nc.createVariable("spatial_ref", "i4")
            sr.assignValue(0)
            for k, v in spatial_ref_attrs.items():
                try:
                    setattr(sr, k, v)
                except Exception:
                    pass
            # Ensure grid_mapping points to spatial_ref on all data variables
            for varname in nc.variables:
                if varname not in ("x", "y", "time", "spatial_ref"):
                    nc.variables[varname].grid_mapping = "spatial_ref"
        logger.info("Wrote spatial_ref as dimensionless CF grid_mapping variable")

    logger.info(f"Successfully saved result to: {destination}")
