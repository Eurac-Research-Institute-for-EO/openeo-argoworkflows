# Staged Data Flow

## The Pattern

```
load_collection → save_result → context → run_udf(EOAP-CWL)
```

This is the endorsed pattern from VITO/EOEPCA alignment. The idea:
1. Load and process a datacube with standard OpenEO processes
2. `save_result` writes it to the workspace PVC as a file
3. The file path is passed as a CWL input via `context`
4. `run_udf(EOAP-CWL)` runs a CWL tool on that file

## Required Change: `save_result` Must Return File Path

**Current state**: `save_result` in `io.py` writes the file but returns `None`.

**Fix needed**: Add `return str(destination)` at the end of `save_result()` in:
`openeo_argoworkflows/executor/openeo_argoworkflows_executor/extra_processes/process_implementations/io.py`

Without this, `{"from_node": "save1"}` resolves to `None` and no file path reaches `run_udf`.

## Process Graph Example

Once `save_result` returns the file path:

```json
{
  "load1": {
    "process_id": "load_collection",
    "arguments": {
      "id": "SENTINEL2_L2A",
      "spatial_extent": {"west": 11.0, "east": 11.1, "south": 46.0, "north": 46.1},
      "temporal_extent": ["2023-06-01", "2023-06-10"]
    }
  },
  "save1": {
    "process_id": "save_result",
    "arguments": {
      "data": {"from_node": "load1"},
      "format": "netCDF"
    }
  },
  "run1": {
    "process_id": "run_udf",
    "arguments": {
      "data": null,
      "udf": "https://example.com/my-tool.cwl",
      "runtime": "EOAP-CWL",
      "context": {
        "input_file": {
          "class": "File",
          "path": {"from_node": "save1"}
        }
      }
    },
    "result": true
  }
}
```

## How It Resolves

1. pg-parser executes `load1` → returns xarray datacube
2. pg-parser executes `save1` → writes `RESULTS/{uuid}.nc`, returns path string e.g. `/user_workspaces/.../RESULTS/abc123.nc`
3. pg-parser resolves `{"from_node": "save1"}` → `/user_workspaces/.../RESULTS/abc123.nc`
4. `context` becomes `{"input_file": {"class": "File", "path": "/user_workspaces/.../RESULTS/abc123.nc"}}`
5. `run_udf()` calls `run_cwl(cwl=udf, inputs=context)`
6. Calrissian receives `inputs.json` with the CWL File input pointing to the staged file

## CWL Tool Side

The CWL tool must declare the input as a `File` type:

```yaml
cwlVersion: v1.2
class: CommandLineTool

requirements:
  DockerRequirement:
    dockerPull: "osgeo/gdal:latest"

baseCommand: gdalinfo

inputs:
  input_file:
    type: File
    inputBinding:
      position: 1

outputs:
  result:
    type: stdout

stdout: gdalinfo-output.txt
```

## Status

- `save_result` returning file path: **NOT YET IMPLEMENTED** (issue #93)
- Real geospatial CWL example: **NOT YET WRITTEN** (issue #93)
