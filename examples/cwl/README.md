# CWL Tools for openEO — Developer Guide

## Overview

CWL (Common Workflow Language) tools are executed via `run_udf` with `runtime="EOAP-CWL"`.
Calrissian runs the tool as a Kubernetes pod. The input data file is staged automatically
from the shared PVC into the tool pod.

## Available Example Tools

| Tool | Description | Input | Output |
|------|-------------|-------|--------|
| `gdalinfo-tool.cwl` | Runs `gdalinfo` on the staged file | NetCDF from `save_result` | `gdalinfo.txt` + STAC |
| `ndvi-tool.cwl` | Computes NDVI from B04/B08 bands | NetCDF with B04+B08 | `ndvi.tif` (Float32) + STAC |

## Process Graph Pattern

Every CWL job follows this pattern:

```
load_collection → save_result → run_udf(EOAP-CWL)
```

`save_result` serialises the in-memory dataset to a NetCDF on the shared PVC.
`run_udf` injects it as a staged `File` input named `openeo_data` into the CWL tool.

### Python client example

```python
import openeo

conn = openeo.connect("https://openeo.eurac.edu")
conn.authenticate_oidc()

pg = {
    "load1": {
        "process_id": "load_collection",
        "arguments": {
            "id": "HYPERECOS_S2_L2A_Sciliar_Catinaccio",
            "spatial_extent": {"west": 11.5, "south": 46.4, "east": 11.6, "north": 46.5},
            "temporal_extent": ["2023-06-01", "2023-06-05"],
            "bands": ["B04", "B08"]
        }
    },
    "save1": {
        "process_id": "save_result",
        "arguments": {
            "data": {"from_node": "load1"},
            "format": "NetCDF"
        }
    },
    "run1": {
        "process_id": "run_udf",
        "arguments": {
            "data": {"from_node": "save1"},
            "udf": "https://raw.githubusercontent.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows/eurac-main/examples/cwl/ndvi-tool.cwl",
            "runtime": "EOAP-CWL",
            "context": {}
        },
        "result": True
    }
}

job = conn.create_job(pg)
job.start_and_wait()
results = job.get_results()
results.download_files("./output/")
```

## Writing a CWL Tool

### Conventions

| Rule | Detail |
|------|--------|
| Input name | Always `openeo_data` — the executor injects the staged file here |
| Input type | Always `File` — never `string` (the file path isn't visible inside tool pods) |
| Docker images | Use `ghcr.io/` prefix — Docker Hub rate limits cause `ImagePullBackOff` |
| STAC output | Produce `catalog.json` (Collection) + `item.json` (Feature) for passthrough |
| Asset hrefs | Use relative paths (`./file.txt`) — executor rewrites them to absolute |

### Minimal tool template

```yaml
cwlVersion: v1.2
class: CommandLineTool

requirements:
  DockerRequirement:
    dockerPull: "ghcr.io/your-org/your-image:tag"
  InitialWorkDirRequirement:
    listing:
      - entryname: run.py
        entry: |
          import sys
          input_path = sys.argv[1]   # path to staged file inside the tool pod
          # ... your processing ...

baseCommand: ["python3", "run.py"]

inputs:
  openeo_data:
    type: File
    inputBinding:
      position: 1

outputs:
  result_file:
    type: File
    outputBinding:
      glob: "output.tif"
  stac_item:
    type: File
    outputBinding:
      glob: "item.json"
  stac_catalog:
    type: File
    outputBinding:
      glob: "catalog.json"
```

### STAC output format

For results to be downloadable via `job.get_results()`, the tool must produce:

**`catalog.json`** — STAC Collection:
```json
{
  "type": "Collection",
  "id": "my-result",
  "stac_version": "1.0.0",
  "description": "...",
  "license": "proprietary",
  "extent": {
    "spatial": {"bbox": [[-180, -90, 180, 90]]},
    "temporal": {"interval": [["2023-06-01T00:00:00Z", null]]}
  },
  "links": [{"rel": "item", "href": "./item.json", "type": "application/geo+json"}]
}
```

**`item.json`** — STAC Item:
```json
{
  "type": "Feature",
  "stac_version": "1.0.0",
  "id": "my-result-item",
  "geometry": null,
  "bbox": null,
  "properties": {"datetime": "2023-06-01T00:00:00Z"},
  "links": [],
  "assets": {
    "output.tif": {
      "href": "./output.tif",
      "type": "image/tiff; application=geotiff",
      "roles": ["data"]
    }
  }
}
```

## Chaining CWL Tools

To run two CWL tools in sequence (second tool receives first tool's output):

```python
"run2": {
    "process_id": "run_udf",
    "arguments": {
        "data": {"from_node": "save1"},      # first tool reads the NetCDF
        "udf": "https://.../ndvi-tool.cwl",
        "runtime": "EOAP-CWL",
        "context": {}
    }
},
"run1": {
    "process_id": "run_udf",
    "arguments": {
        "data": {"from_node": "run2"},       # second tool reads ndvi.tif
        "udf": "https://.../gdalinfo-tool.cwl",
        "runtime": "EOAP-CWL",
        "context": {}
    },
    "result": True
}
```

> **Note:** openEO only supports one `result: true` node per job. Two independent
> CWL tools that should both produce downloadable output require two separate jobs.

## Debugging

### Stream logs during a job

```bash
# Find the executor pod
kubectl get pods -n openeo --sort-by=.metadata.creationTimestamp | tail -10

# Stream logs (Calrissian output is captured here)
kubectl logs -n openeo <pod-name> -c openeo-argo -f
```

### Read logs after job completes

```bash
# Via kubectl (pods kept for 7 days)
kubectl logs -n openeo <pod-name> -c openeo-argo

# Via Python client
conn.job("<job-id>").logs()
```

### Inspect CWL work directory

Even after the tool pod is gone, staged files remain on the PVC:

```bash
ls /user_workspaces/<user_id>/<job_id>/_cwl_work/
```

### Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `ImagePullBackOff` | Docker Hub rate limit | Switch image to `ghcr.io/` prefix |
| `Did not find output file with glob pattern` | Tool script failed before writing output | Check logs for Python/script errors |
| `Band 'B04' not found` | Wrong bands loaded in `load_collection` | Ensure `bands=["B04","B08"]` in process graph |
| Tool receives wrong path | Used `string` input type instead of `File` | Always use `type: File` for `openeo_data` |
| Only one tool's output visible | Dead branch in process graph | Chain tools with `from_node`, not parallel branches |
