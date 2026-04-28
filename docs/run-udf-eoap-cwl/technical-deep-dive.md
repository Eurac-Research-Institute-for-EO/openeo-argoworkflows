# EOAP-CWL Runtime: Technical Deep Dive

## Overview

This document explains what happens technically when a user submits a job
using `run_udf` with `runtime="EOAP-CWL"` — from process graph submission
to downloadable result.

---

## 1. System Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster (eosao42)                     │
│                                                                         │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐  │
│  │   API Pod        │    │  Argo Workflow    │    │  Executor Pod    │  │
│  │                  │    │  Controller       │    │                  │  │
│  │  FastAPI         │    │                  │    │  openEO process  │  │
│  │  + OIDC auth     │    │  Schedules and   │    │  graph runner    │  │
│  │  + job routing   │    │  monitors jobs   │    │  + Calrissian    │  │
│  └────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘  │
│           │                       │                        │            │
│           └───────────────────────┴───────────── PVC ──────┘            │
│                                              /user_workspaces            │
│                                                                         │
│  ┌──────────────────┐    ┌──────────────────┐                          │
│  │  PostgreSQL      │    │  CWL Tool Pod    │                          │
│  │                  │    │  (per-job,       │                          │
│  │  jobs table      │    │   ephemeral)     │                          │
│  │  status, wfname  │    │  e.g. gdalinfo   │                          │
│  └──────────────────┘    └────────┬─────────┘                          │
│                                   │                                     │
│                              PVC (subset)                               │
│                         /_cwl_work/.../output                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Process Graph Execution Flow

### Example process graph
```
load_collection → save_result → run_udf(EOAP-CWL, gdalinfo-tool.cwl)
```

### Step-by-step execution

```
USER (Python client)
  │
  ├─► POST /jobs          → API stores process graph in PostgreSQL
  │
  └─► POST /jobs/{id}/results
           │
           ▼
      Argo Workflow created → Executor Pod launched
           │
           ▼
┌──────────────────────────────────────────────────────────┐
│  EXECUTOR POD                                            │
│                                                          │
│  Reads process graph, walks nodes in order:             │
│                                                          │
│  ① load_collection                                       │
│     ├─ queries stac.eurac.edu for metadata              │
│     ├─ builds odc-stac lazy load spec                   │
│     └─ returns xarray.Dataset (lazy, not yet computed)  │
│                │                                         │
│                ▼                                         │
│  ② save_result(data=lazy_dataset)                        │
│     ├─ calls dataset.compute()  ← Dask runs here        │
│     │    └─ fetches tiles from S3/object store          │
│     ├─ writes RESULTS/{uuid}.nc to PVC                  │
│     ├─ runs raster2stac → writes STAC/ on PVC           │
│     └─ returns "/user_workspaces/.../RESULTS/{uuid}.nc" │
│                │                                         │
│                ▼ (file path string)                      │
│  ③ run_udf(data=path, runtime="EOAP-CWL", udf=url)      │
│     ├─ detects EOAP-CWL runtime                         │
│     ├─ converts path to CWL File object:                │
│     │    {"class":"File","location":"file:///...nc"}    │
│     └─ calls run_cwl() → Calrissian                     │
│                │                                         │
│                ▼                                         │
│  ④ CALRISSIAN (in-process inside executor pod)          │
│     ├─ downloads gdalinfo-tool.cwl from GitHub          │
│     ├─ reads the File input from PVC                    │
│     │    (executor pod has /user_workspaces mounted)    │
│     ├─ stages file to CWL work dir on PVC               │
│     ├─ launches CWL Tool Pod                            │
│     │         │                                         │
│     │         ▼                                         │
│     │  ┌─────────────────────────────────────────┐     │
│     │  │  CWL TOOL POD  (gdalinfo container)     │     │
│     │  │                                         │     │
│     │  │  Volume mounts:                         │     │
│     │  │    /var/lib/cwl/stg{uuid}/  ← PVC      │     │
│     │  │    (staged input file is here)          │     │
│     │  │                                         │     │
│     │  │  /user_workspaces NOT mounted here ⚠️   │     │
│     │  │                                         │     │
│     │  │  Runs: python3 run.py /var/lib/cwl/...  │     │
│     │  │    └─ gdalinfo {staged_file}            │     │
│     │  │    └─ writes gdalinfo.txt               │     │
│     │  │    └─ writes item.json, catalog.json    │     │
│     │  └─────────────────────────────────────────┘     │
│     │         │                                         │
│     ├─ collects outputs from CWL work dir              │
│     └─ writes stdout JSON (cwl_outputs.json)           │
│                │                                         │
│                ▼                                         │
│  ⑤ STAC Passthrough (_collect_calrissian_outputs)       │
│     ├─ scans CWL outdir for catalog/collection.json     │
│     ├─ rmtree(STAC/)  ← wipes raster2stac output       │
│     ├─ copytree(cwl_outdir → STAC/)                     │
│     ├─ renames catalog.json → {job_id}.json            │
│     ├─ moves item.json → STAC/items/                    │
│     └─ rewrites asset hrefs to absolute paths           │
│                                                          │
│  Job status → "finished" in PostgreSQL                  │
└──────────────────────────────────────────────────────────┘
           │
           ▼
USER calls job.get_results()
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│  API Pod                                                 │
│                                                          │
│  reads STAC/{job_id}.json       (Collection)            │
│  reads STAC/items/item.json     (finds asset hrefs)     │
│  signs each href → time-limited download URL            │
│  returns collection dict with signed URLs               │
└──────────────────────────────────────────────────────────┘
           │
           ▼
     GET signed URL → download gdalinfo.txt ✅
```

---

## 3. The Dask ↔ CWL Boundary

This is the most important concept in the architecture.

```
┌─────────────────────────────┐      ┌──────────────────────────────┐
│      DASK WORLD             │      │       CWL WORLD              │
│                             │      │                              │
│  load_collection            │      │  run_udf(EOAP-CWL)          │
│  filter_bbox                │  ◄── │                              │
│  reduce_dimension    ──────►│ FILE │  gdalinfo-tool.cwl          │
│  save_result         ──────►│ ON   │  ndvi-tool.cwl              │
│                             │  PVC │  any-eoap-tool.cwl          │
│  In-memory xarray           │      │                              │
│  (cannot cross pods)        │      │  Separate Kubernetes pod     │
│                             │      │  per job (via Calrissian)    │
└─────────────────────────────┘      └──────────────────────────────┘
         executor pod                         CWL tool pod
```

**Why the boundary exists**: CWL tools run in separate Docker containers
as Kubernetes pods. There is no shared memory. The only bridge is the
shared PVC (network filesystem). `save_result` is the serialisation step
that writes the in-memory Dask result to a file on the PVC.

---

## 4. Why `openeo_data` Must Be a CWL `File` Type (Not `string`)

```
                     PVC (/user_workspaces/...)
                            │
           ┌────────────────┴────────────────┐
           │                                 │
    EXECUTOR POD                       CWL TOOL POD
    mounts PVC at:                     mounts PVC at:
    /user_workspaces  ✅               /var/lib/cwl/stg{uuid}/ only
                                       /user_workspaces  ❌ NOT MOUNTED

    ─────────────────────────────────────────────────────

    If openeo_data = string "/user_workspaces/...nc":
      → CWL tool receives "/user_workspaces/...nc"
      → gdalinfo tries to open it
      → ❌ ERROR: No such file or directory

    If openeo_data = {"class":"File","location":"file:///...nc"}:
      → Calrissian (in executor pod) reads the file from PVC ✅
      → Copies it to /var/lib/cwl/stg{uuid}/ (tool pod can see this)
      → CWL tool receives "/var/lib/cwl/stg{uuid}/result.nc"
      → gdalinfo opens it
      → ✅ Real raster metadata returned
```

---

## 5. STAC Output Structure

After a successful CWL job with STAC passthrough:

```
/user_workspaces/{user_id}/{job_id}/
│
├── RESULTS/
│   └── {uuid}.nc              ← intermediate file from save_result
│                                 (kept, used as input to CWL)
│
└── STAC/
    ├── {job_id}.json          ← Collection (renamed from catalog.json)
    ├── gdalinfo.txt           ← data file from CWL tool
    │
    └── items/
        └── item.json          ← STAC Item with absolute asset href
                                  href: "/user_workspaces/.../STAC/gdalinfo.txt"
```

The API reads `STAC/{job_id}.json`, follows items, and generates
signed download URLs for each asset href.

---

## 6. Key Conventions for CWL Tool Authors

| Convention | Rule |
|------------|------|
| Staged input name | Always use `openeo_data` as the CWL input name |
| Staged input type | Always `File` — never `string` |
| STAC output | Produce `catalog.json` (Collection type) + `item.json` for passthrough |
| Docker images | Use `ghcr.io/` prefix — Docker Hub hits rate limits on the cluster |
| File hrefs in STAC | Use relative paths (`./file.txt`) — executor rewrites to absolute |

---

## 7. Process Graph Pattern

```python
pg = {
    "load1": {
        "process_id": "load_collection",
        "arguments": {
            "id": "MY_COLLECTION",
            "spatial_extent": {...},
            "temporal_extent": [...]
        }
    },
    "save1": {
        "process_id": "save_result",
        "arguments": {
            "data": {"from_node": "load1"},
            "format": "NetCDF"
        }
        # returns: "/user_workspaces/.../RESULTS/{uuid}.nc"
    },
    "run1": {
        "process_id": "run_udf",
        "arguments": {
            "data": {"from_node": "save1"},   # ← file path injected as openeo_data
            "udf": "https://.../my-tool.cwl",
            "runtime": "EOAP-CWL",
            "context": {}                      # ← extra CWL inputs go here
        },
        "result": True
    }
}
```

> **Note**: Never put `{"from_node": "..."}` inside `context`. The executor
> only resolves `from_node` at top-level arguments, not inside nested dicts.
> Use the `data` parameter for passing node outputs — the executor injects
> it automatically as `openeo_data`.
