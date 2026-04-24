cwlVersion: v1.2
class: CommandLineTool

doc: |
  Runs gdalinfo on a staged raster file (NetCDF, GeoTIFF, etc.) and produces
  a plain-text report alongside a STAC Collection + item.

  Used to validate Phase 5 staged data flow:
    load_collection → save_result → run_udf(EOAP-CWL, gdalinfo-tool.cwl)

  The `input_path` argument is the absolute path to the raster file on the
  shared PVC (returned by save_result in the openEO process graph).

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

          input_path = sys.argv[1]
          now = datetime.now(timezone.utc).isoformat()

          # Run gdalinfo
          result = subprocess.run(
              ["gdalinfo", input_path],
              capture_output=True, text=True
          )
          report = result.stdout if result.returncode == 0 else result.stderr
          with open("gdalinfo.txt", "w") as f:
              f.write(report)
          print(report)

          # STAC item
          item = {
              "type": "Feature",
              "stac_version": "1.0.0",
              "id": "gdalinfo-output",
              "geometry": None,
              "bbox": None,
              "properties": {"datetime": now},
              "links": [],
              "assets": {
                  "gdalinfo.txt": {
                      "href": "./gdalinfo.txt",
                      "type": "text/plain",
                      "roles": ["data"]
                  }
              },
              "collection": "gdalinfo-result"
          }
          with open("item.json", "w") as f:
              json.dump(item, f, indent=2)

          # STAC collection
          collection = {
              "type": "Collection",
              "id": "gdalinfo-result",
              "stac_version": "1.0.0",
              "description": "gdalinfo report from openEO CWL job",
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

          print("Written gdalinfo.txt, item.json, catalog.json")

baseCommand: ["python3", "run.py"]

inputs:
  input_path:
    type: string
    inputBinding:
      position: 1

outputs:
  gdalinfo_report:
    type: File
    outputBinding:
      glob: "gdalinfo.txt"
  stac_item:
    type: File
    outputBinding:
      glob: "item.json"
  stac_catalog:
    type: File
    outputBinding:
      glob: "catalog.json"
