# Issue #98: Mixed STAC Output — Root Cause & Fix

## What Was the Problem?

When running a CWL job via `run_udf(EOAP-CWL)`, the final `STAC/` directory
contained a mix of files from two different sources:

```
STAC/
├── a7ddf756-....json          ← CWL tool's collection  ✅
├── gdalinfo.txt               ← CWL tool's output      ✅
├── items/
│   ├── item.json              ← CWL tool's item        ✅
│   └── 20230602....json       ← raster2stac item       ❌ shouldn't be here
├── 20230602.../
│   └── 20230602....nc         ← raster2stac data file  ❌ shouldn't be here
└── inline_items.csv           ← raster2stac artifact   ❌ shouldn't be here
```

Users downloading results saw unexpected files alongside their CWL output.

---

## Why Did It Happen?

The key is understanding **where raster2stac runs** in the executor.

### What we thought

```
execute(process_graph)
  └─ load_collection → save_result [raster2stac here?] → run_udf(CWL)
                                                              └─ rmtree STAC/
                                                              └─ copytree CWL output
```

We assumed raster2stac was called inside `save_result`. If that were true,
the rmtree inside `run_udf` would wipe it before CWL output was written.

### What actually happens

```
cli.py
  │
  ├─ execute(process_graph)          ← step 1
  │    └─ load_collection
  │    └─ save_result                ← writes RESULTS/{uuid}.nc only
  │    └─ run_udf(CWL)
  │         └─ Calrissian runs
  │         └─ rmtree(STAC/)         ← wipes nothing yet (STAC/ is empty)
  │         └─ copytree(CWL → STAC/) ← CWL output written ✅
  │
  └─ raster2stac runs here           ← step 2, AFTER execute() returns ❌
       └─ reads RESULTS/*.nc
       └─ writes to STAC/            ← pollutes the CWL STAC output
```

`save_result` (in `io.py`) only writes the NetCDF file to `RESULTS/`.
Raster2stac is called separately in `cli.py` after `execute()` returns.

This means raster2stac always ran **after** the CWL STAC was already built,
writing its own files into the same `STAC/` directory.

---

## Timeline of a Job (Before Fix)

```
T=0   execute() starts

T=10s load_collection completes → lazy xarray Dataset

T=45s save_result completes
      └─ RESULTS/result.nc written ✅
      └─ STAC/ is still empty at this point

T=46s run_udf(CWL) starts
      └─ Calrissian runs gdalinfo-tool.cwl
      └─ CWL tool pod produces: gdalinfo.txt, item.json, catalog.json
      └─ rmtree(STAC/)                 ← nothing to delete (empty)
      └─ copytree(cwl_outdir → STAC/)
           STAC/gdalinfo.txt           ✅
           STAC/item.json              ✅ (→ moved to items/)
           STAC/catalog.json           ✅ (→ renamed to {job_id}.json)

T=60s execute() returns ← CWL STAC looks correct here

T=61s raster2stac starts in cli.py
      └─ reads RESULTS/result.nc
      └─ writes to STAC/
           STAC/20230602.../...nc      ❌ added on top of CWL output
           STAC/items/20230602....json ❌
           STAC/inline_items.csv       ❌
           STAC/{job_id}.json          ❌ overwrites CWL collection!

T=75s Job marked finished
      STAC/ = mixed mess of CWL + raster2stac files
```

---

## The Fix

One condition in `cli.py` — skip raster2stac entirely for CWL jobs:

```python
# Before
if result_files:
    # ... raster2stac ...

# After
if result_files and not is_cwl:
    # ... raster2stac ...
```

Where `is_cwl` is detected before `execute()` runs:

```python
parsed_graph = OpenEOProcessGraph(pg_data=openeo_parameters.process_graph)
is_cwl = _is_cwl_job(parsed_graph.pg_data)   # ← added

execute(parsed_graph=parsed_graph)
```

`_is_cwl_job()` already existed in `executor.py` — it looks for `run_udf`
with `runtime=EOAP-CWL` (or the legacy `run_cwl`) in the process graph.

---

## Timeline After Fix

```
T=0   execute() starts

T=60s execute() returns
      STAC/ contains only CWL tool output ✅

T=61s raster2stac check: is_cwl=True → SKIPPED ✅

T=61s Job marked finished
      STAC/ = clean CWL output only ✅
```

---

## Why Didn't rmtree Catch It?

The rmtree in `cwl.py` exists to clean up any raster2stac output that was
written to `STAC/` **before** the CWL tool runs. But in this architecture,
`save_result` does NOT call raster2stac — so `STAC/` is empty when rmtree
runs. The rmtree was correct but defending against the wrong thing.

Raster2stac only ran **after** everything else was done, making rmtree useless
for this case. The fix at the source (skip raster2stac entirely for CWL jobs)
is the right place to fix it.

---

## Files Changed

| File | Change |
|------|--------|
| `executor/openeo_argoworkflows_executor/cli.py` | Import `_is_cwl_job`, detect CWL job before `execute()`, skip raster2stac block |
