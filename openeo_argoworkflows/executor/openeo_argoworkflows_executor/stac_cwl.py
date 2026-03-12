"""Minimal STAC generation for CWL workflow output files.

CWL workflows can produce arbitrary file types (not just NetCDF).
This module creates simple STAC collections and items for any output
files so they appear in the openEO job results endpoint.
"""

import json
import logging
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Common media types for CWL outputs
MEDIA_TYPES = {
    ".tif": "image/tiff; application=geotiff",
    ".tiff": "image/tiff; application=geotiff",
    ".nc": "application/x-netcdf",
    ".json": "application/json",
    ".geojson": "application/geo+json",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".xml": "application/xml",
    ".gml": "application/gml+xml",
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
}


def _get_media_type(filepath: str) -> str:
    """Get the media type for a file based on its extension."""
    ext = Path(filepath).suffix.lower()
    if ext in MEDIA_TYPES:
        return MEDIA_TYPES[ext]
    guessed, _ = mimetypes.guess_type(filepath)
    return guessed or "application/octet-stream"


def create_cwl_stac(
    job_id: str,
    result_files: list,
    stac_path: str,
    stac_api_url: str,
):
    """Create a STAC collection and items for CWL output files.

    Parameters
    ----------
    job_id : str
        The openEO job ID, used as the STAC collection ID.
    result_files : list
        List of absolute file paths to CWL output files.
    stac_path : str
        Directory to write STAC JSON files.
    stac_api_url : str
        URL of the STAC API for publishing.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Build STAC collection
    collection = {
        "type": "Collection",
        "id": job_id,
        "stac_version": "1.0.0",
        "description": f"CWL workflow results for openEO job {job_id}",
        "links": [],
        "title": f"Job {job_id} results",
        "extent": {
            "spatial": {"bbox": [[-180, -90, 180, 90]]},
            "temporal": {"interval": [[now, None]]},
        },
        "license": "proprietary",
    }

    # Build STAC items — one per output file
    items = []
    for filepath in result_files:
        filename = Path(filepath).name
        file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        item_id = Path(filepath).stem

        item = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": item_id,
            "geometry": None,
            "bbox": None,
            "properties": {
                "datetime": now,
            },
            "links": [],
            "assets": {
                filename: {
                    "href": filepath,
                    "type": _get_media_type(filepath),
                    "title": filename,
                    "file:size": file_size,
                    "roles": ["data"],
                }
            },
            "collection": job_id,
        }
        items.append(item)

    # Write collection JSON
    os.makedirs(stac_path, exist_ok=True)
    collection_file = os.path.join(stac_path, f"{job_id}.json")
    with open(collection_file, "w") as f:
        json.dump(collection, f, indent=2)
    logger.info(f"Wrote CWL STAC collection to {collection_file}")

    # Write individual item JSON files to items/ directory
    # The API's get_results() reads STAC/items/*.json to build signed asset URLs
    items_dir = os.path.join(stac_path, "items")
    os.makedirs(items_dir, exist_ok=True)
    for item in items:
        item_file = os.path.join(items_dir, f"{item['id']}.json")
        with open(item_file, "w") as f:
            json.dump(item, f, indent=2)
    logger.info(f"Wrote {len(items)} CWL STAC items to {items_dir}")

    # POST to STAC API
    try:
        requests.post(stac_api_url, json=collection)
        for item in items:
            requests.post(f"{stac_api_url.rstrip('/')}/{job_id}/items", json=item)
        logger.info(f"Published CWL STAC to {stac_api_url}")
    except Exception as e:
        logger.warning(f"Failed to publish CWL STAC to API: {e}")
