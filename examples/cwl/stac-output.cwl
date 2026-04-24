cwlVersion: v1.2
class: CommandLineTool

doc: |
  Test CWL tool that produces a minimal STAC catalog alongside its output.
  Used to validate Phase 4 STAC passthrough in the openEO EOAP-CWL runtime.

requirements:
  DockerRequirement:
    dockerPull: "python:3.11-alpine"
  InitialWorkDirRequirement:
    listing:
      - entryname: run.py
        entry: |
          import json
          import sys
          from datetime import datetime, timezone

          message = sys.argv[1]
          now = datetime.now(timezone.utc).isoformat()

          # Write output file
          with open("output.txt", "w") as f:
              f.write(message + "\n")

          # Write STAC item
          item = {
              "type": "Feature",
              "stac_version": "1.0.0",
              "id": "output",
              "geometry": None,
              "bbox": None,
              "properties": {"datetime": now},
              "links": [],
              "assets": {
                  "output.txt": {
                      "href": "./output.txt",
                      "type": "text/plain",
                      "roles": ["data"]
                  }
              },
              "collection": "stac-output-test"
          }
          with open("item.json", "w") as f:
              json.dump(item, f, indent=2)

          # Write STAC catalog root
          collection = {
              "type": "Collection",
              "id": "stac-output-test",
              "stac_version": "1.0.0",
              "description": "CWL STAC passthrough test",
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


          print(f"Written output.txt, item.json, catalog.json")

baseCommand: ["python3", "run.py"]

inputs:
  message:
    type: string
    inputBinding:
      position: 1

outputs:
  output_file:
    type: File
    outputBinding:
      glob: "output.txt"
  stac_item:
    type: File
    outputBinding:
      glob: "item.json"
  stac_catalog:
    type: File
    outputBinding:
      glob: "catalog.json"
