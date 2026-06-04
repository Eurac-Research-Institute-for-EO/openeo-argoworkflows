"""
CRS extraction from STAC item properties.

Supports proj extension v1 (proj:epsg, proj:wkt2) and v2 (proj:code).
Priority: proj:wkt2 > proj:epsg > proj:code > default EPSG:4326
"""
import logging

import pyproj

logger = logging.getLogger(__name__)


def _extract_crs(item) -> pyproj.CRS:
    """Return a pyproj.CRS from a STAC item's projection properties.

    Falls back to EPSG:4326 if no projection fields are present.
    """
    props = item.properties

    if "proj:wkt2" in props:
        crs = pyproj.CRS.from_wkt(props["proj:wkt2"])
        logger.info("Using CRS from proj:wkt2: %s", crs.name)
        return crs

    if "proj:epsg" in props:
        crs = pyproj.CRS.from_epsg(props["proj:epsg"])
        logger.info("Using CRS from proj:epsg: %s", props["proj:epsg"])
        return crs

    if "proj:code" in props:
        crs = pyproj.CRS.from_string(props["proj:code"])
        logger.info("Using CRS from proj:code: %s", props["proj:code"])
        return crs

    logger.warning("No CRS found in item properties, defaulting to EPSG:4326")
    return pyproj.CRS.from_epsg(4326)
