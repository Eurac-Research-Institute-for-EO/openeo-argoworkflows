cwlVersion: v1.2
class: CommandLineTool

doc: |
  Computes NDVI from a staged multi-band NetCDF produced by save_result.
  Expects bands B04 (Red) and B08 (NIR) in the input file.

  NDVI = (B08 - B04) / (B08 + B04)

  Uses GDAL Python bindings only — no pip install at runtime.
  Output: ndvi.tif (Float32, values -1 to 1) + STAC Collection + Item.

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
          import sys
          import traceback
          from datetime import datetime, timezone

          import numpy as np
          from osgeo import gdal

          gdal.UseExceptions()

          def main():
              input_path = sys.argv[1]
              now = datetime.now(timezone.utc).isoformat()

              # Discover subdatasets to find B04 and B08 variables
              ds = gdal.Open(input_path)
              subdatasets = ds.GetSubDatasets()
              print(f"Subdatasets in {input_path}:")
              for s in subdatasets:
                  print(f"  {s}")

              if not subdatasets:
                  # Single-variable file — try opening directly
                  subdatasets = [(input_path, "")]

              def find_sub(name):
                  matches = [s[0] for s in subdatasets if name in s[0] or name in s[1]]
                  if not matches:
                      raise ValueError(
                          f"Band '{name}' not found. Available: {[s[0] for s in subdatasets]}"
                      )
                  return matches[0]

              b04_path = find_sub("B04")
              b08_path = find_sub("B08")
              print(f"B04: {b04_path}")
              print(f"B08: {b08_path}")

              b04_ds = gdal.Open(b04_path)
              b08_ds = gdal.Open(b08_path)

              b04 = b04_ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
              b08 = b08_ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
              print(f"B04 shape: {b04.shape}, B08 shape: {b08.shape}")

              # Compute NDVI
              denom = b08 + b04
              ndvi = np.where(denom != 0, (b08 - b04) / denom, 0).astype(np.float32)
              ndvi = np.clip(ndvi, -1, 1)
              print(f"NDVI min={ndvi.min():.4f} max={ndvi.max():.4f} mean={ndvi.mean():.4f}")

              # Write output GeoTIFF
              driver = gdal.GetDriverByName("GTiff")
              rows, cols = ndvi.shape
              out_ds = driver.Create("ndvi.tif", cols, rows, 1, gdal.GDT_Float32)
              out_ds.SetGeoTransform(b04_ds.GetGeoTransform())
              out_ds.SetProjection(b04_ds.GetProjection())
              band = out_ds.GetRasterBand(1)
              band.WriteArray(ndvi)
              band.SetNoDataValue(-9999)
              band.SetDescription("NDVI")
              out_ds.FlushCache()
              out_ds = None
              print("Written ndvi.tif")

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
                      "ndvi.tif": {
                          "href": "./ndvi.tif",
                          "type": "image/tiff; application=geotiff",
                          "roles": ["data"],
                          "title": "NDVI GeoTIFF"
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

          try:
              main()
          except Exception as e:
              print(f"ERROR: {e}", file=sys.stderr)
              traceback.print_exc()
              # Write error report so it shows up as a downloadable asset
              with open("error.txt", "w") as f:
                  f.write(traceback.format_exc())
              sys.exit(1)

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
      glob: "ndvi.tif"
  stac_item:
    type: File
    outputBinding:
      glob: "item.json"
  stac_catalog:
    type: File
    outputBinding:
      glob: "catalog.json"
