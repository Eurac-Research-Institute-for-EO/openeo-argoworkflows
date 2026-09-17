# Implementation

## What Was Built (PR #91 — merged to eurac-main 2026-04-22)

### API Changes

**File**: `openeo_argoworkflows/api/patches/openeo_fastapi_core.py`
- `get_udf_runtimes()` now returns the `EOAP-CWL` runtime entry

**File**: `openeo_argoworkflows/api/openeo_argoworkflows_api/specs/run_udf.json` (new)
- Process spec for `run_udf` — auto-loaded by `app.py` at startup

### Executor Changes

**File**: `openeo_argoworkflows/executor/openeo_argoworkflows_executor/executor.py`
- `_is_cwl_job()` extended to detect `run_udf` nodes where `arguments.runtime` is `EOAP-CWL` (case-insensitive)

**File**: `openeo_argoworkflows/executor/openeo_argoworkflows_executor/extra_processes/process_implementations/cwl.py`
- `run_udf()` shim added — maps `udf→cwl`, `context→inputs`, calls `run_cwl()`
- `run_cwl()` kept as-is (deprecated alias, still works)

**File**: `openeo_argoworkflows/executor/openeo_argoworkflows_executor/extra_processes/specs/run_udf.json` (new)
- Executor-side spec — required by the specs `__init__.py` auto-loader

### Infra Fixes (also in PR #91)

**Files**: `openeo_argoworkflows/api/.devcontainer/Dockerfile` and `openeo_argoworkflows/executor/.devcontainer/Dockerfile`
- Upgraded base image: `mcr.microsoft.com/vscode/devcontainers/python:0-3.11-bullseye` → `mcr.microsoft.com/devcontainers/python:1-3.11-bookworm`
- Removed broken Yarn apt source (`/etc/apt/sources.list.d/yarn.list`) before `apt update`
- API Dockerfile: `postgresql-13` → `postgresql-15`

## Key Files Reference

| File | Purpose |
|------|---------|
| `api/patches/openeo_fastapi_core.py` | `get_udf_runtimes()` — populates `/udf_runtimes` |
| `api/openeo_argoworkflows_api/app.py` | Auto-loads all `specs/*.json` into `/processes` |
| `api/openeo_argoworkflows_api/specs/run_udf.json` | `run_udf` process spec (API) |
| `executor/executor.py` | `_is_cwl_job()` — detects CWL jobs and bypasses Dask |
| `executor/extra_processes/process_implementations/cwl.py` | `run_udf()` + `run_cwl()` — Calrissian invocation |
| `executor/extra_processes/process_implementations/io.py` | `save_result()` — needs to return file path (not yet done) |
| `executor/extra_processes/specs/run_udf.json` | `run_udf` process spec (executor) |
| `executor/stac_cwl.py` | Generates STAC collection + items for CWL outputs |

## How `_is_cwl_job()` Works

```python
def _is_cwl_job(pg_data: dict) -> bool:
    for node_id, node in pg_data.items():
        if not isinstance(node, dict):
            continue
        pid = node.get("process_id")
        if pid == "run_cwl":              # legacy
            return True
        if pid == "run_udf":
            args = node.get("arguments", {})
            runtime = args.get("runtime", "")
            if isinstance(runtime, str) and runtime.lower() == "eoap-cwl":
                return True
    return False
```

When a CWL job is detected, execution bypasses the Dask tiling pipeline entirely
and calls `_execute_cwl()` directly.

## Calrissian In-Process Invocation

Calrissian is called **in-process** (not subprocess) so that the monkey-patch to
`KubernetesPodVolumeInspector` takes effect. This patch makes Calrissian use the
`main` container (not the Argo `wait` sidecar at `containers[0]`) for PVC volume
mount resolution.
