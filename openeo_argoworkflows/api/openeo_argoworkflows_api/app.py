
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.responses import RedirectResponse

from openeo_fastapi.client.psql.engine import get
from openeo_argoworkflows_api.s3 import is_s3_uri, s3_download_stream

from openeo_fastapi.api.app import OpenEOApi
from openeo_fastapi.api.types import Billing, Plan, FileFormat, GisDataType
from openeo_fastapi.client.core import OpenEOCore
from openeo_pg_parser_networkx.process_registry import Process as pgProcess

from openeo_argoworkflows_api.jobs import ArgoJobsRegister
from openeo_argoworkflows_api.files import ArgoFileRegister
from openeo_argoworkflows_api.settings import ExtendedAppSettings


gtif = FileFormat(
   title="GTiff",
    gis_data_types=[GisDataType("raster")],
    parameters={},
)

netcdf = FileFormat(
   title="netCDF",
    gis_data_types=[GisDataType("raster")],
    parameters={},
)

input_formats = [ gtif, netcdf ]
output_formats = [ netcdf ]

links = []

settings = ExtendedAppSettings()

client = OpenEOCore(
    settings=settings,
    files=ArgoFileRegister(settings=settings, links=links),
    jobs=ArgoJobsRegister(settings=settings, links=links),
    input_formats=input_formats,
    output_formats=output_formats,
    links=links,
    billing=Billing(
        currency="credits",
        default_plan="a-cloud",
        plans=[Plan(name="user", description="Subscription plan.", paid=True)],
    )
)
app = FastAPI()

app.router.add_api_route(
    name="file_headers",
    path=f"/openeo/{client.settings.OPENEO_VERSION}/files" + "/{path:path}",
    response_model=None,
    response_model_exclude_unset=False,
    response_model_exclude_none=True,
    methods=["HEAD"],
    endpoint=client.files.file_header,
)

api = OpenEOApi(client=client, app=app)

# Register custom EURAC processes (not in upstream openeo-processes-dask)
_specs_dir = Path(__file__).parent / "specs"
for spec_file in _specs_dir.glob("*.json"):
    with open(spec_file) as f:
        spec = json.load(f)
    api.client.processes.process_registry[("predefined", spec["id"])] = pgProcess(spec)
# Clear the cached process list so it includes the new processes
api.client.processes.get_available_processes.cache_clear()

api.app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=[
        "Accept-Ranges",
        "Content-Encoding",
        "Content-Range",
        "Range",
        "Link",
        "Location",
        "OpenEO-Costs",
        "OpenEO-Identifier",
        "Authorization",
    ],
    expose_headers=[
        "Accept-Ranges",
        "Content-Encoding",
        "Content-Range",
        "Link",
        "Location",
        "OpenEO-Costs",
        "OpenEO-Identifier",
    ],
)

def s3_proxy_download(job_id: uuid.UUID, filename: str):
    """Proxy download for S3 result files — bypasses browser CORS restrictions.

    Fetches the file from CEPH S3 internally and streams it to the client.
    The browser talks to openeo.eurac.edu (no cross-origin request needed).
    """
    from openeo_argoworkflows_api.jobs import ArgoJob
    from openeo_argoworkflows_api.settings import ExtendedAppSettings

    job = get(get_model=ArgoJob, primary_key=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    settings = ExtendedAppSettings()
    import glob, json as _json
    stac_items_dir = Path(settings.OPENEO_WORKSPACE_ROOT) / str(job.user_id) / str(job_id) / "STAC" / "items"
    href = None
    for item_path in glob.glob(str(stac_items_dir / "*.json")):
        with open(item_path) as f:
            item = _json.load(f)
        for asset in item.get("assets", {}).values():
            if asset.get("href", "").endswith(filename) and is_s3_uri(asset.get("href", "")):
                href = asset["href"]
                break
        if href:
            break

    if not href:
        raise HTTPException(status_code=404, detail=f"Result file '{filename}' not found or not on S3")

    try:
        body, content_length, content_type = s3_download_stream(href)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch from S3: {e}")

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if content_length:
        headers["Content-Length"] = str(content_length)

    return StreamingResponse(body.iter_chunks(), media_type=content_type, headers=headers)


def redirect_wellknown():
    return RedirectResponse("/.well-known/openeo")

api.app.router.add_api_route(
    name="redirect_wellknown",
    path=f"/{client.settings.OPENEO_VERSION}/.well-known/openeo",
    response_model=None,
    response_model_exclude_unset=False,
    response_model_exclude_none=True,
    methods=["GET"],
    endpoint=redirect_wellknown,
)

api.app.router.add_api_route(
    name="redirect_wellknown",
    path=f"/openeo/{client.settings.OPENEO_VERSION}/.well-known/openeo",
    response_model=None,
    response_model_exclude_unset=False,
    response_model_exclude_none=True,
    methods=["GET"],
    endpoint=redirect_wellknown,
)

api.app.router.add_api_route(
    name="s3_proxy_download",
    path=f"{client.settings.OPENEO_PREFIX}/jobs/{{job_id}}/results/download/{{filename}}",
    response_model=None,
    methods=["GET", "HEAD"],
    endpoint=s3_proxy_download,
)

app = api.app