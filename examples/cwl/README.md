# run_cwl MVP — Developer Guide

## Overview

The `run_cwl` process executes CWL (Common Workflow Language) workflows
through the openEO backend. CWL documents are validated with `cwltool`
and executed via Calrissian on Kubernetes.

## Quick Start

### 1. Validate locally with cwltool

```bash
# Install cwltool
pip install cwltool

# Validate the golden-path workflow
cwltool --validate examples/cwl/echo-tool.cwl

# Run locally (requires Docker)
cwltool examples/cwl/echo-tool.cwl examples/cwl/echo-inputs.json
```

Expected output: a file `output.txt` containing `Hello from openEO run_cwl MVP`.

### 2. Submit via openEO API

```bash
# Create a job with the run_cwl process graph
curl -X POST https://<backend>/jobs \
  -H "Authorization: Bearer oidc/eurac/<token>" \
  -H "Content-Type: application/json" \
  -d '{
    "process": {
      "process_graph": {
        "run1": {
          "process_id": "run_cwl",
          "arguments": {
            "cwl": "https://raw.githubusercontent.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows/main/examples/cwl/echo-tool.cwl",
            "inputs": {
              "message": "Hello from openEO"
            }
          },
          "result": true
        }
      }
    }
  }'

# Start the job
curl -X POST https://<backend>/jobs/<job-id>/results \
  -H "Authorization: Bearer oidc/eurac/<token>"

# Check status
curl https://<backend>/jobs/<job-id> \
  -H "Authorization: Bearer oidc/eurac/<token>"

# Download results
curl https://<backend>/jobs/<job-id>/results \
  -H "Authorization: Bearer oidc/eurac/<token>"
```

### 3. Submit via Python client

```python
import openeo

conn = openeo.connect("https://<backend>")
conn.authenticate_oidc()

job = conn.create_job(
    process_graph={
        "run1": {
            "process_id": "run_cwl",
            "arguments": {
                "cwl": "https://raw.githubusercontent.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows/main/examples/cwl/echo-tool.cwl",
                "inputs": {
                    "message": "Hello from openEO"
                }
            },
            "result": True
        }
    }
)
job.start()
job.logs()
results = job.get_results()
results.download_files("./output/")
```

## Process Graph Format

```json
{
  "run1": {
    "process_id": "run_cwl",
    "arguments": {
      "cwl": "<inline CWL string or URL>",
      "inputs": {
        "<cwl_input_name>": "<value>"
      },
      "options": {
        "validate_only": false,
        "max_ram": "8G",
        "max_cores": 4
      }
    },
    "result": true
  }
}
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `cwl` | string | yes | Inline CWL document (YAML/JSON) or URL to a `.cwl` file |
| `inputs` | object | yes | Key-value mapping of CWL input parameter names to values |
| `options` | object | no | Execution options (see below) |

### Options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `validate_only` | boolean | `false` | Validate CWL without executing |
| `max_ram` | string | `"8G"` | Max RAM for Calrissian (e.g. `"16G"`) |
| `max_cores` | integer | `4` | Max CPU cores for Calrissian |

## Architecture

```
User submits openEO job with run_cwl process
    │
    ▼
API creates job → PostgreSQL → Redis queue
    │
    ▼
Executor pod starts (Argo Workflow)
    │
    ├─ Detects run_cwl → skips Dask cluster setup
    │
    ▼
Resolves CWL (download URL or write inline)
    │
    ▼
Validates with cwltool --validate
    │
    ▼
Invokes Calrissian subprocess
    │   └─ Calrissian creates K8s pods for each CWL step
    │
    ▼
Collects outputs → copies to workspace RESULTS/
    │
    ▼
Generates STAC collection + items
    │
    ▼
Job status → finished
```

## Debugging

### Check executor logs

```bash
# Find the Argo workflow pod
kubectl get pods -n openeo -l OPENEO_JOB_ID=<job-id>

# Check executor logs
kubectl logs -n openeo <pod-name> -c main

# Look for CWL-specific log lines:
# "CWL job detected — skipping Dask cluster setup"
# "Running Calrissian: ..."
# "Calrissian stderr: ..."
# "Collected N output files to ..."
```

### Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `CWL validation failed` | Invalid CWL document | Validate locally first: `cwltool --validate your.cwl` |
| `CWL execution failed (exit code 1)` | Calrissian step pod failed | Check Calrissian stderr in executor logs |
| `Failed to resolve CWL document` | URL unreachable or invalid inline CWL | Verify URL is accessible from the cluster |
| `CWL execution timed out` | Workflow exceeds 1-hour timeout | Simplify workflow or split into smaller steps |

## Infrastructure Requirements

For cluster execution, the following must be configured:

1. **Executor image**: Must include `calrissian` and `cwltool` packages
2. **RBAC**: Executor service account needs pod create/delete/watch permissions
   (Calrissian creates pods for each CWL step)
3. **Storage**: RWX PVC for Calrissian working directories

## Known Limitations (MVP)

- **CWL v1.0–1.2 only**: Only `CommandLineTool` and simple single-step `Workflow` are tested
- **No ScatterFeatureRequirement**: Parallel scatter is not supported
- **No SubworkflowFeatureRequirement**: Nested workflows are not tested
- **No private registries**: Docker images must be publicly accessible
- **No secrets**: CWL steps cannot access Kubernetes secrets
- **1-hour timeout**: Long-running workflows will be terminated
- **File outputs only**: CWL outputs must be `File` type (not `Directory`)
- **No CWL → openEO data cube bridging**: CWL outputs are raw files, not xarray cubes
- **STAC for non-NetCDF is minimal**: Only file metadata (no spatial/temporal extent)
