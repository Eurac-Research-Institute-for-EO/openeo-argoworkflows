# Testing

## Verify Endpoints

```bash
# Check EOAP-CWL appears in /udf_runtimes
curl -sk https://openeo.eurac.edu/openeo/1.1.0/udf_runtimes | jq .

# Check run_udf appears in /processes
curl -sk https://openeo.eurac.edu/openeo/1.1.0/processes | jq '.processes[] | select(.id=="run_udf")'
```

## Basic CWL Test (no geo-processing)

Uses `integer-input.cwl` — a simple tool that echoes a message N times.

```python
import openeo

conn = openeo.connect("https://openeo.eurac.edu/openeo/1.1.0")
conn.authenticate_oidc()

pg = {
    "run1": {
        "process_id": "run_udf",
        "arguments": {
            "data": None,
            "udf": "https://raw.githubusercontent.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows/eurac-main/examples/cwl/integer-input.cwl",
            "runtime": "EOAP-CWL",
            "context": {
                "count": 3,
                "message": "HelloFromRunUdf"
            }
        },
        "result": True
    }
}

job = conn.create_job(process_graph=pg, title="test run_udf basic")
job.start_and_wait()
print("Status:", job.status())
print("Results:", job.get_results().get_assets())
```

Expected: `output.txt` containing `HelloFromRunUdf` printed 3 times.

## Known Issues with `echo-tool.cwl`

`echo-tool.cwl` uses `$(inputs.message)` embedded in a shell string with `shellQuote: false`.
When passed to `sh -c`, the shell interprets `$(...)` as command substitution and it
expands to empty string. Output is just `\n`. Use `integer-input.cwl` instead for testing.

## Checking Job Logs

### Via Grafana (Loki)
`http://eosao42.eurac.edu:31000` → Explore → Label: `namespace=openeo`

### Via kubectl
```bash
# Get workflow name from DB
PGPASS=$(kubectl get secret openeo-postgresql -n openeo -o jsonpath='{.data.postgres-password}' | base64 -d)
kubectl exec -n openeo openeo-postgresql-0 -- env PGPASSWORD="$PGPASS" \
  psql -U postgres -d postgres -c "SELECT workflowname FROM jobs WHERE job_id='<job-id>';"

# Get executor logs
kubectl logs -n openeo -l workflows.argoproj.io/workflow=<workflow-name> --container main
```

### Check output files directly
```bash
kubectl exec -n openeo deployment/openeo-openeo-argo -- \
  find /user_workspaces/<user-id>/<job-id>/ -type f
```

## Argo UI

`http://eosao42.eurac.edu:31635` — view workflow execution, step logs, pod status.
