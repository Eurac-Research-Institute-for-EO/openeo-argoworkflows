# VITO/EOEPCA Research Findings

Research conducted: 2026-04-10 (initial) + 2026-04-22 (staged data flow)
Sources: `openeo-geopyspark-driver`, `openeo-processes-dask` (Emile Sonneveld meeting)

## Key Decisions Aligned with VITO

| Decision | Value |
|----------|-------|
| Runtime name | `"EOAP-CWL"` (case-insensitive) |
| `udf` parameter | CWL document — inline string, URL, or file path |
| `context` parameter | CWL inputs dict (key-value pairs passed to Calrissian) |
| `data` parameter | Passed as None, no validation |
| STAC output | CWL MUST produce `catalog.json` / `collection.json` |
| CWL logs | Disabled (`CALRISSIAN_STREAM_LOGS=NO`) — not implemented Phase 1 |

## `/udf_runtimes` Response Structure

Exact structure used by VITO:
```json
{
  "EOAP-CWL": {
    "default": "1",
    "title": "EOAP-CWL",
    "type": "language",
    "versions": {
      "1": { "libraries": {} }
    }
  }
}
```

## CWL Source Resolution (`CwlSource.from_any()`)

Priority order:
1. URL starting with `http://` or `https://` → fetch remote file
2. File path ending in `.cwl` or `.yaml` with no newlines → read local file
3. Anything else → treat as inline CWL string

## STAC Output Detection

VITO's `find_stac_root()` searches in order:
1. `catalog.json`
2. `catalogue.json`
3. `collection.json`

If none found → unhandled error. CWL workflow MUST produce one of these.

## Staged Data Flow Mechanism

### How `save_result → context` works

The mechanism uses **standard OpenEO `from_node` references** — no special construct.

In the process graph:
```json
{
  "save1": {
    "process_id": "save_result",
    "arguments": { "data": {"from_node": "load1"} }
  },
  "run1": {
    "process_id": "run_udf",
    "arguments": {
      "udf": "https://example.com/tool.cwl",
      "runtime": "EOAP-CWL",
      "context": {
        "input_file": {"class": "File", "path": {"from_node": "save1"}}
      }
    }
  }
}
```

The pg-parser resolves `{"from_node": "save1"}` → return value of `save_result` → file path string.

### VITO backend key files
- `openeogeotrellis/backend.py` → `run_cwl()` receives context as `cwl_arguments`
- `openeogeotrellis/integrations/calrissian.py` → `cwl_to_stac()` stages context as JSON input file for Calrissian

### Context serialization
When `context` is a dict, VITO serializes it to JSON and stages it as a file at
`/calrissian/input-data/...` via an input staging job before launching the main CWL workflow.

## Anti-Patterns (do NOT do these)

- **Do NOT embed `load_stac` inside CWL** — not portable across backends
- **Do NOT re-load STAC output as datacube and re-save as GeoTIFF** — destroys metadata
- **Do NOT trust arbitrary Docker images** — scoped to APEX/ESA partners only for now
