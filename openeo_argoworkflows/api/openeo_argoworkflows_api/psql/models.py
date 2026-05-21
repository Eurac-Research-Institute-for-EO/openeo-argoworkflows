from typing import Optional
from openeo_fastapi.client.jobs import Job
from openeo_fastapi.client.psql.settings import BASE
from openeo_fastapi.client.psql.models import *

class ArgoJobORM(JobORM):

    workflowname = Column(VARCHAR, nullable=True)
    """The name of the argo workflow."""

    message = Column(VARCHAR, nullable=True)
    """User-facing message describing the current job state or failure reason."""


class ArgoJob(Job):

    workflowname: Optional[str]
    """The name of the argo workflow."""

    message: Optional[str] = None
    """User-facing message describing the current job state or failure reason."""

    @classmethod
    def get_orm(cls):
        return ArgoJobORM


metadata = BASE.metadata
