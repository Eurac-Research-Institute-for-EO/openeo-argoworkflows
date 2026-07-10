from typing import Tuple, Union

import numpy as np
import shapely
from pydantic import BaseModel
from pyproj import CRS, Geod
from shapely import Polygon, box, geometry


class GridCorners(BaseModel):
    lower_left: tuple[Union[int, float], Union[int, float]]
    lower_right: tuple[Union[int, float], Union[int, float]]
    upper_left: tuple[Union[int, float], Union[int, float]]
    upper_right: tuple[Union[int, float], Union[int, float]]


class StacGrid:
    def __init__(self, bbox, tilesize, crs) -> None:
        self.bbox = geometry.box(*bbox)
        self.edges = self.derive_points(self.bbox)

        self.tilesize = tilesize

        self.crs = CRS(crs)

        self._cells = None

    @property
    def get_cells(self):
        """ """
        if self._cells:
            return self._cells
        self._cells = self.derive_cells()
        return self._cells

    @classmethod
    def derive_points(cls, bbox: Polygon):
        """ """
        minx, miny, maxx, maxy = bbox.bounds

        lower_left = (minx, miny)
        lower_right = (maxx, miny)
        upper_left = (minx, maxy)
        upper_right = (maxx, maxy)
        return GridCorners(
            lower_left=lower_left,
            lower_right=lower_right,
            upper_left=upper_left,
            upper_right=upper_right,
        )

    @staticmethod
    def derive_distance(crs, point1, point2):
        """Returning distance in metres divided by the resolution in metres."""
        geod = Geod(
            a=crs.ellipsoid.semi_major_metre, rf=crs.ellipsoid.inverse_flattening
        )
        az12, az21, distance = geod.inv(point1[0], point1[1], point2[0], point2[1])
        return distance

    @classmethod
    def find_cell_bounds(cls, crs, cell, starting_position):
        lon, lat = starting_position

        geod = Geod(
            a=crs.ellipsoid.semi_major_metre, rf=crs.ellipsoid.inverse_flattening
        )

        y_range, x_range = cell

        yN, yM = y_range
        min_lon, tmp_lat, _ = geod.fwd(lon, lat, 90, yN)
        max_lon, _, _ = geod.fwd(lon, lat, 90, yM)

        xN, xM = x_range
        _, min_lat, _ = geod.fwd(min_lon, tmp_lat, 180, xN)
        _, max_lat, _ = geod.fwd(min_lon, tmp_lat, 180, xM)

        return box(min_lon, min_lat, max_lon, max_lat)

    def set_grid_cells(self):
        """ """

        lon_distance = self.derive_distance(
            self.crs, self.edges.upper_left, self.edges.upper_right
        )
        lat_distance = self.derive_distance(
            self.crs, self.edges.upper_left, self.edges.lower_left
        )

        cells = []
        n_lon_tiles = int(np.ceil(lon_distance / self.tilesize))
        n_lat_tiles = int(np.ceil(lat_distance / self.tilesize))

        for long_cell in range(n_lon_tiles):
            if ((long_cell + 1) * self.tilesize) > lon_distance:
                long_cell_pos = (long_cell * self.tilesize, lon_distance)
            else:
                long_cell_pos = (
                    long_cell * self.tilesize,
                    ((long_cell + 1) * self.tilesize) - 1,
                )

            for lat_cell in range(n_lat_tiles):
                if ((lat_cell + 1) * self.tilesize) > lat_distance:
                    lat_cell_pos = (lat_cell * self.tilesize, lat_distance)

                else:
                    lat_cell_pos = (
                        lat_cell * self.tilesize,
                        ((lat_cell + 1) * self.tilesize) - 1,
                    )

                bounds = self.find_cell_bounds(
                    self.crs, [long_cell_pos, lat_cell_pos], self.edges.upper_left
                )

                cells.append([long_cell_pos, lat_cell_pos, bounds])

        self.cells = cells
