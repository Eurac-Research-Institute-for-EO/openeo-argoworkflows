# Architecture

## Overview

CWL workflows are executed via the standard OpenEO `run_udf` process using the
`EOAP-CWL` runtime. Calrissian (a CWL runner for Kubernetes) executes the workflow
inside an Argo Workflow pod.

## Full Execution Flow

```
User
 │
 ▼
OpenEO Python Client
 │  POST /jobs  (process graph with run_udf + runtime=EOAP-CWL)
 ▼
OpenEO API  (openeo.eurac.edu)
 │  Store job in PostgreSQL
 │  Submit Argo workflow
 ▼
Argo Workflows
 │  Spawn executor pod
 ▼
Executor Pod
 │  _is_cwl_job() detects run_udf + EOAP-CWL
 │  run_udf() maps: udf→cwl, context→inputs
 │  Write workflow.cwl + inputs.json to PVC (_cwl_work/)
 ▼
Calrissian (in-process)
 │  Validate CWL
 │  Launch CWL step pods on Kubernetes
 ▼
CWL Step Pods  (Docker image declared in CWL)
 │  Execute user's tool (GDAL, SNAP, Python, etc.)
 │  Write outputs to Calrissian outdir on PVC
 ▼
Executor Pod (post-processing)
 │  Collect outputs → RESULTS/
 │  Generate STAC collection + items → STAC/
 │  Update job status → finished
 ▼
User
    GET /jobs/{id}/results  →  signed asset URLs  →  download files
```

## Staged Data Pattern

The endorsed pattern for passing OpenEO data into a CWL workflow:

```
load_collection
      │
      ▼
 save_result  ──────────────────────────────────────┐
      │  writes NetCDF/GeoTIFF to RESULTS/           │
      │  returns file path (string)                  │
      ▼                                              │
 run_udf                                             │
   runtime = "EOAP-CWL"                             │
   udf     = CWL document or URL                    │
   context = { "input_file": {"from_node": "save1"} } ◄─┘
      │
      ▼
 Calrissian runs CWL tool with input_file = /path/to/file.nc
```

## Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| API (`app.py`, `openeo_fastapi_core.py`) | Expose `run_udf` in `/processes`, `EOAP-CWL` in `/udf_runtimes` |
| Executor (`executor.py`) | Detect CWL jobs (`_is_cwl_job`), bypass Dask tiling |
| CWL impl (`cwl.py`) | `run_udf()` shim, `run_cwl()` core, Calrissian invocation |
| `stac_cwl.py` | Generate STAC collection/items for non-STAC CWL outputs |
| Calrissian | CWL runner on Kubernetes — spawns step pods, manages PVC paths |
