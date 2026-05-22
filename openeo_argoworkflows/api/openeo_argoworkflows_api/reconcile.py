"""
Reconciliation script — syncs DB job status against Argo workflow state.

Run as a Kubernetes CronJob every 5 minutes. Finds all jobs stuck at
'running' whose Argo workflow has already ended, and updates their status
and message accordingly.
"""
import logging
import os

from openeo_fastapi.api.types import Status
from openeo_fastapi.client.psql.engine import get_engine, modify

from openeo_argoworkflows_api.psql.models import ArgoJob, ArgoJobORM
from openeo_argoworkflows_api.workflows import WorkflowsService

from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


def _detect_oom(workflow) -> bool:
    nodes = getattr(workflow.status, "nodes", None) or {}
    for node in nodes.values():
        msg = (getattr(node, "message", None) or "").lower()
        if "oomkilled" in msg or "out of memory" in msg:
            return True
    return False


def reconcile():
    argo_server = os.environ["ARGO_WORKFLOWS_SERVER"]
    argo_namespace = os.environ["ARGO_WORKFLOWS_NAMESPACE"]
    argo_token = os.environ["ARGO_WORKFLOWS_TOKEN"]

    argo = WorkflowsService(
        host=argo_server,
        verify_ssl=False,
        namespace=argo_namespace,
        token=argo_token,
    )

    engine = get_engine()
    Session = sessionmaker(bind=engine)
    with Session() as session:
        rows = session.query(ArgoJobORM).filter(
            ArgoJobORM.status == Status.running.value
        ).all()
        running_jobs = [ArgoJob.from_orm(r) for r in rows]

    if not running_jobs:
        logger.info("No running jobs found — nothing to reconcile.")
        return

    logger.info(f"Reconciling {len(running_jobs)} running job(s).")

    for job in running_jobs:
        if not job.workflowname:
            job.status = Status.error
            job.message = "Job has no associated workflow and will not complete. Please resubmit."
            modify(job)
            logger.warning(f"Job {job.job_id} had no workflowname — marked as error.")
            continue

        try:
            workflow = argo.get_workflow(
                name=job.workflowname,
                namespace=argo_namespace,
            )
            phase = getattr(workflow.status, "phase", None)
        except Exception:
            job.status = Status.error
            job.message = "Workflow could not be found. It may have been deleted or expired."
            modify(job)
            logger.warning(f"Job {job.job_id} workflow {job.workflowname} not found — marked as error.")
            continue

        if phase == "Succeeded":
            job.status = Status.finished
            job.message = None
            modify(job)
            logger.info(f"Job {job.job_id} reconciled → finished.")
        elif phase in ("Failed", "Error"):
            if _detect_oom(workflow):
                job.message = (
                    "Job exceeded available memory. "
                    "Try reducing the temporal extent or spatial area and retry."
                )
            else:
                job.message = "Job failed. Please check the logs for details."
            job.status = Status.error
            modify(job)
            logger.info(f"Job {job.job_id} reconciled → error (phase={phase}).")
        else:
            logger.info(f"Job {job.job_id} still active (phase={phase}) — skipping.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    reconcile()
