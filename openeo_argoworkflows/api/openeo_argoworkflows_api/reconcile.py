"""
Reconciliation script — syncs DB job status against Argo workflow state.

Run as a Kubernetes CronJob every 5 minutes. Finds all jobs stuck at
'running' whose Argo workflow has already ended, and updates their status
and message accordingly.
"""
import logging

from openeo_fastapi.api.types import Status
from openeo_fastapi.client.psql.engine import get_all, modify

from openeo_argoworkflows_api.psql.models import ArgoJob
from openeo_argoworkflows_api.settings import ExtendedAppSettings
from openeo_argoworkflows_api.tasks import _detect_oom

from argo_workflows.exceptions import NotFoundException
from openeo_argoworkflows_api.workflows import WorkflowsService

logger = logging.getLogger(__name__)


def reconcile():
    settings = ExtendedAppSettings()

    argo = WorkflowsService(
        host=settings.ARGO_WORKFLOWS_SERVER,
        verify_ssl=False,
        namespace=settings.ARGO_WORKFLOWS_NAMESPACE,
        token=settings.ARGO_WORKFLOWS_TOKEN.get_secret_value(),
    )

    running_jobs = [
        j for j in get_all(get_model=ArgoJob)
        if j.status == Status.running
    ]

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
                namespace=settings.ARGO_WORKFLOWS_NAMESPACE,
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
