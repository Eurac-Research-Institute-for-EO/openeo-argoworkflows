# Roadmap

## Completed

| Phase | PR | Description |
|-------|----|-------------|
| 1. Research & Alignment | — | VITO/EOEPCA standard, runtime=EOAP-CWL, parameter mapping |
| 2. API Changes | #91 | `/udf_runtimes` + `run_udf` in `/processes` |
| 3. Executor Changes | #91 | `_is_cwl_job()` + `run_udf()` shim in `cwl.py` |

## Remaining

### Phase 4 — STAC Output Passthrough (issue #93)

**Problem**: When a CWL workflow produces its own STAC (`catalog.json` / `collection.json`),
the executor currently ignores it and generates a flat STAC via `stac_cwl.py` instead.

**Fix**: In `_collect_calrissian_outputs()` (`cwl.py`):
1. After Calrissian runs, check if `catalog.json`, `catalogue.json`, or `collection.json` exists in outdir
2. If yes → copy the STAC tree to `STAC/` as-is (passthrough)
3. If no → fall back to current `stac_cwl.py` behavior (generate flat STAC from output files)

---

### Phase 5 — Staged Data Flow (issue #93)

**Problem**: `save_result` in `io.py` returns `None` — the file path never reaches `run_udf`.

**Fix**: Add `return str(destination)` at the end of `save_result()` in:
`openeo_argoworkflows/executor/openeo_argoworkflows_executor/extra_processes/process_implementations/io.py`

See [staged-data-flow.md](staged-data-flow.md) for full details and process graph example.

---

### Phase 6 — Real Geospatial CWL Example (issue #93)

**Problem**: No CWL tool exists that demonstrates the full staged data pattern with real geo-processing.

**What to build**: A CWL tool in `examples/cwl/` that:
- Accepts a GeoTIFF or NetCDF as a `File` input
- Runs a real geospatial operation (e.g. `gdalinfo`, NDVI, reprojection via GDAL/SNAP/Python)
- Produces output files (ideally with a STAC catalog for Phase 4 testing)

**Suggested first example**: `gdalinfo-tool.cwl` using `osgeo/gdal` Docker image — simple,
no dependencies, proves the full pipeline.

---

### Future Considerations

- **Docker image trust**: Currently scoped to APEX/ESA partners only. Needs policy decision before opening to general users.
- **`run_cwl` deprecation**: Once `run_udf(EOAP-CWL)` is fully validated, `run_cwl` should be marked deprecated in its spec JSON.
- **CWL log streaming**: VITO disables it (`CALRISSIAN_STREAM_LOGS=NO`). Consider exposing logs via Loki/Grafana instead.
