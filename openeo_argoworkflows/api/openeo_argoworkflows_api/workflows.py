import json

from hera.workflows import Steps, Workflow, WorkflowsService, Step, Env
from hera.workflows.models import Template, Container, EnvVar, EnvVarSource, Metadata, PersistentVolumeClaimVolumeSource, SecretKeySelector, Volume, VolumeMount

from openeo_argoworkflows_api.settings import ExtendedAppSettings


def executor_workflow(service: WorkflowsService, process_graph: dict, dask_profile: dict, user_profile: dict):
    user_profile_as_json = json.dumps(user_profile)
    dask_profile_as_json = json.dumps(dask_profile)
    process_graph_as_json = json.dumps(process_graph)

    settings = ExtendedAppSettings()
    with Workflow(
        generate_name="openeo-executor-",
        entrypoint="process",
        namespace=service.namespace,
        workflows_service=service,
        pod_metadata=Metadata(
            labels={
                "OPENEO_JOB_ID": user_profile["OPENEO_JOB_ID"],
                "OPENEO_USER_ID": user_profile["OPENEO_USER_ID"]
            }
        ),
        volumes=Volume(
            name="workspaces-volume",
            persistent_volume_claim=PersistentVolumeClaimVolumeSource(
                claim_name="openeo-workspace"
            )
        ),
        deletion_grace_period_seconds=1800,
        active_deadline_seconds=settings.OPENEO_EXECUTOR_DEADLINE,
    ) as w:
        with Steps(name="process"):
            Step(
                name="process-graph",
                template=Template(
                    name="executor",
                    container=Container(
                        env=[
                            Env(name="STAC_API_URL", value=str(settings.STAC_API_URL)),
                            Env(name="OPENEO_COMPUTE_TIMEOUT", value=str(settings.OPENEO_COMPUTE_TIMEOUT)),
                            EnvVar(
                                name="AWS_ACCESS_KEY_ID",
                                value_from=EnvVarSource(
                                    secret_key_ref=SecretKeySelector(name="cdse-s3-credentials", key="AWS_ACCESS_KEY_ID")
                                ),
                            ),
                            EnvVar(
                                name="AWS_SECRET_ACCESS_KEY",
                                value_from=EnvVarSource(
                                    secret_key_ref=SecretKeySelector(name="cdse-s3-credentials", key="AWS_SECRET_ACCESS_KEY")
                                ),
                            ),
                            EnvVar(
                                name="AWS_ENDPOINT_URL_S3",
                                value_from=EnvVarSource(
                                    secret_key_ref=SecretKeySelector(name="cdse-s3-credentials", key="AWS_ENDPOINT_URL_S3")
                                ),
                            ),
                        ],
                        image=settings.OPENEO_EXECUTOR_IMAGE,
                        image_pull_policy="Always",
                        command=["openeo_executor"],
                        args=[
                            "execute",
                            "--process_graph", process_graph_as_json,
                            "--user_profile", user_profile_as_json,
                            "--dask_profile", dask_profile_as_json
                        ],
                        volume_mounts=[
                            VolumeMount(
                                name="workspaces-volume",
                                mount_path=str(settings.OPENEO_WORKSPACE_ROOT)
                            )
                        ]
                    )
                )
            )

    return w

