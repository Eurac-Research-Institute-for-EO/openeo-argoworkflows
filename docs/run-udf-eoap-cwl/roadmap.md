# Roadmap

## Completed

| Phase | PR | Description |
|-------|----|-------------|
| 1. Research & Alignment | — | VITO/EOEPCA standard, runtime=EOAP-CWL, parameter mapping |
| 2. API Changes | #91 | `/udf_runtimes` + `run_udf` in `/processes` |
| 3. Executor Changes | #91 | `_is_cwl_job()` + `run_udf()` shim in `cwl.py` |
| 4. STAC Output Passthrough | eurac-main | Detect CWL-produced STAC, copy to `STAC/`, move items to `items/`, rewrite hrefs |
| 5. Staged Data Flow | eurac-main | `save_result` returns file path; `gdalinfo-tool.cwl` proves full `load_collection → save_result → run_udf(EOAP-CWL)` pipeline |

## Remaining

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
