from datetime import timedelta
from hera.workflows import  WorkflowsService
from openeo_fastapi.api.types import Status
from redis import Redis
from rq import Queue
from typing import Any

from openeo_fastapi.client.psql.engine import modify, get
from openeo_argoworkflows_api.psql.models import ArgoJob
from openeo_argoworkflows_api.workflows import executor_workflow
from openeo_argoworkflows_api.settings import ExtendedAppSettings


def _resolve_udps(process_graph: dict, user_id) -> dict:
    import openeo_processes_dask_slim.specs
    from openeo_pg_parser_networkx import Process as pgProcess, ProcessRegistry
    from openeo_pg_parser_networkx.resolving_utils import resolve_process_graph
    from openeo_fastapi.client.processes import UserDefinedProcessGraph

    process_registry = ProcessRegistry()
    for pid in openeo_processes_dask_slim.specs.__all__:
        process_registry[("predefined", pid)] = pgProcess(getattr(openeo_processes_dask_slim.specs, pid))

    def get_udp_spec(process_id: str, namespace: str) -> dict:
        udp = get(get_model=UserDefinedProcessGraph, primary_key=[process_id, namespace])
        return udp.dict()

    return resolve_process_graph(
        process_graph=process_graph,
        process_registry=process_registry,
        get_udp_spec=get_udp_spec,
        namespace=str(user_id),
    )

settings = ExtendedAppSettings()
q = Queue(
    connection=Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT
))

def queue_to_submit(job: ArgoJob):
    """  Function to see if there is space in the pool for another Job. """
    argo = WorkflowsService(
        host=settings.ARGO_WORKFLOWS_SERVER,
        verify_ssl=False,
        namespace=settings.ARGO_WORKFLOWS_NAMESPACE,
        token=settings.ARGO_WORKFLOWS_TOKEN.get_secret_value(),
    )

    workflows = argo.list_workflows().items

    if not workflows:
        return q.enqueue(submit_job, job)

    check_statuses = ("Running", "Pending")
    filtered_workflows = [
        workflow
        for workflow in workflows
        if workflow.status.phase in check_statuses
    ]

    if len(filtered_workflows) >= settings.ARGO_WORKFLOWS_LIMIT:
        return q.enqueue_in(timedelta(minutes=5), queue_to_submit, job)
    else:
        return q.enqueue(submit_job, job)


def submit_job(job: ArgoJob):
    """ Submit the job to argo. """
    argo = WorkflowsService(
        host=settings.ARGO_WORKFLOWS_SERVER,
        verify_ssl=False,
        namespace=settings.ARGO_WORKFLOWS_NAMESPACE,
        token=settings.ARGO_WORKFLOWS_TOKEN.get_secret_value(),
    )    

    if settings.DASK_GATEWAY_SERVER and settings.OPENEO_EXECUTOR_IMAGE:
        dask_profile = {
            "GATEWAY_URL": settings.DASK_GATEWAY_SERVER,
            "OPENEO_EXECUTOR_IMAGE": settings.OPENEO_EXECUTOR_IMAGE,
            "WORKER_CORES": settings.DASK_WORKER_CORES,
            "WORKER_MEMORY": settings.DASK_WORKER_MEMORY,
            "WORKER_LIMIT": settings.DASK_WORKER_LIMIT,
            "CLUSTER_IDLE_TIMEOUT": settings.DASK_CLUSTER_IDLE_TIMEOUT
        }
    else:
        dask_profile = {
            "LOCAL": True
        }

    user_profile = {
        "OPENEO_JOB_ID": str(job.job_id),
        "OPENEO_USER_ID": str(job.user_id),
        "OPENEO_USER_WORKSPACE": str(settings.OPENEO_WORKSPACE_ROOT / str(job.user_id) / str(job.job_id))
    }
    process_graph = _resolve_udps(job.process.process_graph, job.user_id)
    workflow = executor_workflow(argo, process_graph, dask_profile, user_profile)

    response = workflow.create()

    job.status = Status.running
    job.workflowname = response.metadata.name
    modify(job)

    return q.enqueue(poll_job_status, job, response.metadata)


def _detect_oom(workflow) -> bool:
    """Return True if any workflow node was OOMKilled."""
    nodes = getattr(workflow.status, "nodes", None) or {}
    for node in nodes.values():
        msg = (getattr(node, "message", None) or "").lower()
        if "oomkilled" in msg or "out of memory" in msg:
            return True
    return False


def poll_job_status(job: ArgoJob, metadata: Any):
    """Poll Argo for workflow status and sync to DB."""
    existing = get(get_model=ArgoJob, primary_key=job.job_id)
    if not existing:
        return

    argo = WorkflowsService(
        host=settings.ARGO_WORKFLOWS_SERVER,
        verify_ssl=False,
        namespace=settings.ARGO_WORKFLOWS_NAMESPACE,
        token=settings.ARGO_WORKFLOWS_TOKEN.get_secret_value(),
    )

    try:
        workflow = argo.get_workflow(
            name=metadata.name,
            namespace=metadata.namespace
        )
    except Exception:
        existing.status = Status.error
        existing.message = "Workflow could not be found. It may have been deleted or expired."
        modify(existing)
        return

    phase = getattr(workflow.status, "phase", None)

    if phase == "Succeeded":
        existing.status = Status.finished
        existing.message = None
        modify(existing)
    elif phase in ("Failed", "Error"):
        if _detect_oom(workflow):
            existing.message = (
                "Job exceeded available memory. "
                "Try reducing the temporal extent or spatial area and retry."
            )
        else:
            existing.message = "Job failed. Please check the logs for details."
        existing.status = Status.error
        modify(existing)
    elif phase in ("Running", "Pending"):
        return q.enqueue(poll_job_status, job, metadata)
    else:
        # Unknown or None phase — re-enqueue to retry
        return q.enqueue(poll_job_status, job, metadata)