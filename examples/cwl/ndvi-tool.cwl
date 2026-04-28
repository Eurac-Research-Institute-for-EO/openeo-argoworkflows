cwlVersion: v1.2
class: CommandLineTool

doc: |
  Computes NDVI from a staged multi-band NetCDF produced by save_result.
  Expects bands B04 (Red) and B08 (NIR) in the input file.

  NDVI = (B08 - B04) / (B08 + B04)

  Output: ndvi.nc (Float32, values -1 to 1) + STAC Collection + Item.

  Process graph pattern:
    load_collection(bands=["B04","B08"]) → save_result → run_udf(EOAP-CWL)

requirements:
  DockerRequirement:
    dockerPull: "ghcr.io/osgeo/gdal:ubuntu-full-latest"
  InitialWorkDirRequirement:
    listing:
      - entryname: run.py
        entry: |
          import json
          import subprocess
          import sys
          from datetime import datetime, timezone

          subprocess.run(
              [sys.executable, "-m", "pip", "install", "-q", "xarray", "netcdf4", "rasterio"],
              check=True
          )

          import numpy as np
          import xarray as xr

          input_path = sys.argv[1]
          now = datetime.now(timezone.utc).isoformat()

          print(f"Opening: {input_path}")
          ds = xr.open_dataset(input_path, engine="netcdf4")
          print(f"Variables: {list(ds.data_vars)}")
          print(f"Dims: {dict(ds.dims)}")

          if "B04" not in ds or "B08" not in ds:
              raise ValueError(f"Expected B04 and B08 in dataset, got: {list(ds.data_vars)}")

          b04 = ds["B04"].astype("float32")
          b08 = ds["B08"].astype("float32")

          ndvi = (b08 - b04) / (b08 + b04)
          ndvi = ndvi.clip(-1, 1)
          ndvi.name = "NDVI"
          ndvi.attrs["long_name"] = "Normalized Difference Vegetation Index"
          ndvi.attrs["valid_range"] = [-1.0, 1.0]
          ndvi.attrs["units"] = "1"

          out_ds = ndvi.to_dataset(name="NDVI")

          # Carry over spatial coords and CRS if present
          for coord in ["spatial_ref", "crs"]:
              if coord in ds:
                  out_ds[coord] = ds[coord]

          out_ds.to_netcdf("ndvi.nc")
          print("Written ndvi.nc")

          # STAC item
          item = {
              "type": "Feature",
              "stac_version": "1.0.0",
              "id": "ndvi-output",
              "geometry": None,
              "bbox": None,
              "properties": {"datetime": now},
              "links": [],
              "assets": {
                  "ndvi.nc": {
                      "href": "./ndvi.nc",
                      "type": "application/x-netcdf",
                      "roles": ["data"],
                      "title": "NDVI NetCDF"
                  }
              },
              "collection": "ndvi-result"
          }
          with open("item.json", "w") as f:
              json.dump(item, f, indent=2)

          # STAC collection
          collection = {
              "type": "Collection",
              "id": "ndvi-result",
              "stac_version": "1.0.0",
              "description": "NDVI computed from Sentinel-2 B04 and B08 via openEO CWL job",
              "license": "proprietary",
              "extent": {
                  "spatial": {"bbox": [[-180, -90, 180, 90]]},
                  "temporal": {"interval": [[now, None]]}
              },
              "links": [
                  {"rel": "item", "href": "./item.json", "type": "application/geo+json"}
              ]
          }
          with open("catalog.json", "w") as f:
              json.dump(collection, f, indent=2)

          print("Written item.json, catalog.json")
          ds.close()

baseCommand: ["python3", "run.py"]

inputs:
  openeo_data:
    type: File
    inputBinding:
      position: 1

outputs:
  ndvi_result:
    type: File
    outputBinding:
      glob: "ndvi.nc"
  stac_item:
    type: File
    outputBinding:
      glob: "item.json"
  stac_catalog:
    type: File
    outputBinding:
      glob: "catalog.json"
