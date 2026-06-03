# The S3 Migration Story

## The Problem

The OpenEO backend had been running on a single VM (`eosao42.eurac.edu`) with a 451 GB SSD. Every time a user ran a job, the result files landed on that disk — GeoTIFFs, NetCDFs, STAC catalogs — and they stayed there. As more researchers across Europe started using the platform, the disk quietly filled up. Then one day ICT reported the server was down: memory full. The Dask workers had been sitting idle for a full hour after each job finished (the idle timeout was set to 3600 seconds), consuming RAM until the machine ran out.

We fixed the immediate crisis — reduced the Dask idle timeout to 300 seconds — but it was clear that storing job results on the local SSD was never going to scale. We needed a better answer.

## Michele's Suggestion

During a meeting, Michele mentioned the object store. Eurac has a CEPH cluster at `scientificnet.org` with a petabyte-scale S3-compatible interface. There was already a public bucket called `eo-public` with 434 TB free. Michele had uploaded datasets there himself. If job results went to S3 instead of the local disk, storage would effectively be unlimited and the results would survive even if the VM was replaced.

We opened [issue #120](https://github.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows/issues/120) to track the work.

## Testing the Water

Before writing a single line of code, we needed to know the credentials actually worked. Michele shared his S3 credentials for us to test with. We wrote a small script that connected to `https://s3.scientificnet.org`, bucket `eo-public`, and ran four operations: list, write, read back, delete.

The list failed (no `s3:ListBucket` permission on Michele's account) but write, read, and delete all passed. That was fine — we'd never need to list the whole bucket. We access objects by known paths: `<user_id>/<job_id>/<filename>`. We stored the credentials as a Kubernetes secret (`openeo-s3-credentials`) in the `openeo` namespace and moved on.

## The Plan

The architecture was simple on paper:

1. **Executor**: after `save_result` writes a `.nc` file locally, upload it to S3 and return the `s3://` URI instead of the local path.
2. **API**: when `GET /jobs/{id}/results` is called, detect `s3://` hrefs in the STAC items and generate pre-signed HTTPS download URLs instead of serving files through the API pod.

Backward compatible by design: if S3 is not configured or upload fails, fall back to the local path silently. Existing jobs on the PVC keep working.

## Writing the Tests First

Following TDD discipline — RED before GREEN — we wrote the tests before touching any production code.

**Executor tests** (`test_s3_upload.py`): five scenarios covering the happy path, correct boto3 call arguments, S3 key structure (`user_id/job_id/filename`), fallback when env vars are missing, and fallback on upload failure.

**API tests** (`test_s3_results.py`): four scenarios covering `is_s3_uri()` detection, pre-signed URL generation via mocked boto3, passthrough for non-S3 hrefs.

All nine tests were RED. Then we wrote the code.

## The Code

Two new modules:

- **`openeo_argoworkflows_executor/.../s3.py`**: `upload_to_s3(local_path)` — reads S3 credentials from `os.environ`, uploads the file under `<user_id>/<job_id>/<filename>`, returns `s3://eo-public/<key>`. Falls back to local path on any failure.
- **`openeo_argoworkflows_api/s3.py`**: `is_s3_uri(href)` and `generate_presigned_url(href)` — detects S3 URIs and generates 7-day pre-signed HTTPS download URLs.

We wired `upload_to_s3()` into the last line of `save_result` in `io.py`, and added the `is_s3_uri` / `generate_presigned_url` logic into `get_results` in `jobs.py` — S3 hrefs get pre-signed URLs, local paths get the existing signed file endpoint.

All nine tests went GREEN. We added `s3.enabled` flag to the Helm chart and injected the `openeo-s3-credentials` secret into the API pod. Pushed everything to `eurac-main`.

---

## Act II: Everything That Can Go Wrong

### First Deployment: Missing boto3

CI built the images. We ran helm upgrade. The API pod crashed immediately:

```
ModuleNotFoundError: No module named 'boto3'
```

`boto3` wasn't in `pyproject.toml` for either the executor or the API. We added it to both, ran `poetry lock --no-update` to regenerate the lock files, pushed again. CI rebuilt both images.

### Second Deployment: Missing `import os`

The API came up. We submitted the first test job. It went to queue and immediately failed in the RQ worker:

```
NameError: name 'os' is not defined
```

We had added `os.environ.get(s3_var)` to `tasks.py` when forwarding S3 credentials to the executor — but `tasks.py` had no `import os` at the top. One line fix, pushed, deployed.

### Third Deployment: Queue Worker Has No S3 Vars

The job ran. It finished. We checked the STAC item — local path, not `s3://`. The S3 upload hadn't fired.

The API pod had the S3 env vars (we verified with `kubectl exec`). But `tasks.py` runs in the **queue worker** container, not the API container. The Helm chart only injected the S3 secret into the API container. The queue worker — same pod, different container — had nothing.

We added the same S3 secret injection block to the queue worker section of `deployment.yaml`, deployed again (revision 122).

### Fourth Deployment: UserProfile Drops Unknown Fields

Another job, another local path. The queue worker now had the env vars, and they were being forwarded into the `user_profile` JSON passed to the Argo workflow. We could see `S3_ENDPOINT_URL` in the workflow args.

But `UserProfile` in `models.py` is a strict Pydantic model:

```python
class UserProfile(BaseModel):
    OPENEO_USER_ID: str
    OPENEO_JOB_ID: str
    OPENEO_USER_WORKSPACE: Path
```

Pydantic silently drops fields it doesn't know about. The S3 credentials were being passed, parsed, and immediately discarded. The executor never saw them.

Fix: add the four S3 fields as `Optional[str] = None` to `UserProfile`, then in `cli.py` set them as environment variables:

```python
for s3_var in ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
    val = getattr(openeo_parameters.user_profile, s3_var, None)
    if val:
        os.environ[s3_var] = val
```

Pushed, CI green, deployed (revision 123).

### Fifth Deployment: Stale Cached Image on Node

Another job, another local path. At this point we were suspicious. We verified the S3 credentials were now in the `user_profile` JSON in the Argo workflow args — they were there. But still no upload.

The executor pod was running `imagePullPolicy: IfNotPresent` (the default). The node already had an old version of the executor image cached from before our `models.py` and `cli.py` fixes. Kubernetes wasn't pulling the new image — it saw `latest` was already present and skipped the pull.

Fix: add `image_pull_policy="Always"` to the executor container spec in `workflows.py`. Deployed (revision 124). From this point on every new job would pull a fresh executor image.

### Sixth Deployment: Upload Fires at the Wrong Time

Another job, another local path. But now we knew the new image was definitely being used — we ran it inside docker and confirmed `UserProfile` had the S3 fields. The credentials were flowing end-to-end. So why wasn't it working?

We went back and traced the full executor flow after `execute()` runs:

1. `save_result` writes the `.nc` file to `RESULTS/` and returns the local path.
2. `cli.py` collects all files from `RESULTS/` into `result_files`.
3. `raster2stac` processes the local files and creates STAC items — **moving the files into `STAC/<timestamp>/`** — with hrefs pointing to their new location.
4. Our S3 upload code ran, uploading files from `RESULTS/` and building a `href_map` keyed on `RESULTS/` paths.
5. We then scanned STAC item JSONs for hrefs to update — but the hrefs were `STAC/<timestamp>/file.nc`, not `RESULTS/file.nc`. No match. Nothing updated.

We had been uploading the right files to S3 but then failing to patch the STAC items because the hrefs had already been rewritten by raster2stac to a different path.

Fix: instead of building a map from `result_files`, read the STAC item JSONs first, find the actual hrefs in them, and upload whatever local file each href points to:

```python
for item_json in glob(f"{stac_path}/items/*.json"):
    with open(item_json) as f:
        item = json.load(f)
    for asset in item.get("assets", {}).values():
        href = asset.get("href", "")
        if href and os.path.isfile(href):
            s3_uri = upload_to_s3(href)
            if s3_uri != href:
                asset["href"] = s3_uri
    with open(item_json, "w") as f:
        json.dump(item, f)
```

Pushed, CI green, deployed (revision 126). Waiting for test job to confirm.

---

## The Scorecard (so far)

| Attempt | What broke | Fix |
|---------|-----------|-----|
| 1 | `boto3` not in `pyproject.toml` | Added to both API and executor dependencies |
| 2 | `import os` missing in `tasks.py` | Added one import line |
| 3 | S3 env vars not in queue worker container | Added secret injection to queue worker in `deployment.yaml` |
| 4 | Pydantic `UserProfile` silently dropped unknown fields | Added S3 fields to model; set `os.environ` in `cli.py` |
| 5 | Node using stale cached executor image | Set `imagePullPolicy=Always` in executor workflow spec |
| 6 | Upload fired on `RESULTS/` paths; STAC items had `STAC/<timestamp>/` paths | Read actual hrefs from STAC items before uploading |

## What's Still Open (at this point)

- **Permanent S3 credentials**: Michele's credentials are temporary. An ICT ticket (cc Luce Cattani) is needed for dedicated write credentials.
- **CWL-based jobs**: The S3 upload only fires for Python process graph jobs using `save_result`. CWL/raster2stac jobs write results directly to the PVC — a separate integration point.
- **Dask compute hang** ([issue #121](https://github.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows/issues/121)): jobs loading data from external S3 hang indefinitely when the Dask cluster disconnects mid-computation. Needs a timeout on `.compute()` — tracked for the dev environment.

---

## Act III: The File Is There. You Just Can't Have It.

### Seventh Deployment: `unknown-user/unknown-job`

A job finished. For the first time the STAC item had an `s3://` href. Progress. But the path was:

```
s3://eo-public/unknown-user/unknown-job/19900101000000_19900131000000.nc
```

`upload_to_s3()` reads `OPENEO_USER_ID` and `OPENEO_JOB_ID` from `os.environ` to build the S3 key. In `cli.py` these were being set on the Dask `options` object — not on `os.environ`. Two lines added:

```python
os.environ["OPENEO_USER_ID"] = str(openeo_parameters.user_profile.OPENEO_USER_ID)
os.environ["OPENEO_JOB_ID"] = str(openeo_parameters.user_profile.OPENEO_JOB_ID)
```

Deployed (revision 127).

### Eighth Deployment: The File Is On S3

Job finished. STAC item showed:

```
s3://eo-public/94fc9d6f-.../af7f50c3-.../19900101000000_19900131000000.nc
```

We checked S3 directly — 325 MB file confirmed. Correct user ID and job ID in the path. The upload was finally working end-to-end.

The API generated a pre-signed URL using boto3. User tried to download. **403 Forbidden.**

CEPH RadosGW doesn't honour boto3-style AWS Signature V4 pre-signed URLs. But `eo-public` has public read access — a plain `curl` on the file returned 200 OK immediately. We didn't need pre-signed URLs at all.

Rewrote `generate_presigned_url()` to construct a simple direct HTTPS URL:

```python
url = f"{endpoint_url.rstrip('/')}/{without_scheme}"
# → https://s3.scientificnet.org/eo-public/<user_id>/<job_id>/file.nc
```

Updated the tests. Deployed (revision 128).

### Ninth Deployment: The Browser Says No

Direct URL returns 200 OK from `curl`. From the web editor — nothing.

The browser sends an OPTIONS preflight before any cross-origin GET:

```
OPTIONS https://s3.scientificnet.org/eo-public/... 
Origin: https://openeo.eurac.edu
→ HTTP 403 Forbidden
```

CEPH has no CORS policy on the `eo-public` bucket. Without `Access-Control-Allow-Origin`, the browser refuses to fetch even a publicly readable file. Michele's credentials don't have permission to set bucket CORS — only the bucket owner (Luce Cattani / ICT) can do that.

The file is on S3 with the correct path. The URL resolves. `curl` downloads it fine. The browser cannot.

**Two paths forward:**
1. **ICT ticket** — ask Luce Cattani to set `AllowedOrigins: ["*"]`, `AllowedMethods: ["GET", "HEAD"]` on `eo-public`. Same ticket needed for permanent write credentials.
2. **Proxy endpoint** — add a route to the API that fetches from S3 and streams to the client. No CORS issue since the browser talks to `openeo.eurac.edu`. More code, works without waiting on ICT.

---

## The Full Scorecard

| # | What broke | Fix |
|---|-----------|-----|
| 1 | `boto3` not in `pyproject.toml` | Added to both API and executor dependencies |
| 2 | `import os` missing in `tasks.py` | One import line |
| 3 | S3 env vars not in queue worker container | Added secret injection to queue worker in `deployment.yaml` |
| 4 | Pydantic `UserProfile` silently dropped S3 fields | Added fields to model; set `os.environ` in `cli.py` |
| 5 | Stale cached executor image on node | Set `imagePullPolicy=Always` in executor workflow spec |
| 6 | Upload used `RESULTS/` paths; STAC items had `STAC/<timestamp>/` paths | Read actual hrefs from STAC items before uploading |
| 7 | S3 key used `unknown-user/unknown-job` | Set `OPENEO_USER_ID` + `OPENEO_JOB_ID` in `os.environ` |
| 8 | CEPH pre-signed URLs return 403 | Switched to direct public URL (bucket is publicly readable) |
| 9 | CORS preflight blocked by browser | Waiting on ICT CORS policy, or implement proxy endpoint |

## What's Still Open

- **CORS / download**: ICT ticket to Luce Cattani for CORS policy on `eo-public`, or implement a proxy endpoint in the API. This is the last blocker before users can actually download results.
- **Permanent S3 credentials**: Michele's credentials are temporary. Same ICT ticket.
- **CWL-based jobs**: S3 upload only fires for Python process graph jobs using `save_result`. CWL/raster2stac jobs write directly to PVC — separate integration point.
- **Dask compute hang** ([issue #121](https://github.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows/issues/121)): jobs loading data from external S3 hang when Dask cluster disconnects. Needs `.compute()` timeout — tracked for the dev environment.

---

## Where We Left Off

**Last completed:**
- Proxy download endpoint implemented and deployed — `GET /jobs/{id}/results/download/{filename}` streams from CEPH through the API pod, bypasses browser CORS
- HEAD method support added to proxy endpoint (commit `21195f3`)
- Dask compute timeout added — `compute_with_timeout()` wraps `to_netcdf()` with 600s hard deadline, `OPENEO_COMPUTE_TIMEOUT` env var (commit `66c4fa2`, issue #121 resolved)

**Verified end-to-end:**
- Job `4b738df5` → `s3://eo-public/94fc9d6f/.../19900101000000_19900131000000.nc` → proxy download 200 / 325 MB / 54s
- Apache on Andrea's server is correctly forwarding large streaming responses (the ProxyTimeout issue resolved itself or Andrea made the config change)

**Next steps:**
- Close issue #121 on GitHub
- ICT ticket to Luce Cattani for permanent S3 write credentials and CORS policy on `eo-public` (cc Michele)
- CWL S3 integration — CWL jobs bypass `save_result` so uploads don't fire; needs a separate hook in `cwl.py` after Calrissian completes

**Source repo:** `/home/yadagale/eurac-eoepca-plus/openeo-argoworkflows` on branch `eurac-main`
