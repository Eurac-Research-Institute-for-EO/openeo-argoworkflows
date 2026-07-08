import logging
from pathlib import Path

import click

logger = logging.getLogger(__name__)


@click.group()
def cli():
    """Defining group for executor CLI."""
    pass


def _close_dask(client, gateway, local_cluster):
    """Best-effort close of the Dask client, gateway session and LocalCluster.
    Never raises.

    Left open, their comm threads / aiohttp sessions deadlock the interpreter
    at shutdown — the process never exits and the job hangs in "running" even
    though all work completed (#147). The in-process LocalCluster (LOCAL mode)
    must be closed too: its threaded workers hold HDF5 state from the compute
    and deadlock the post-processing xr.open_dataset if left alive (observed
    on stable; gateway mode computes in separate pods and is unaffected).
    """
    for obj in (client, local_cluster, gateway):
        if obj is None:
            continue
        try:
            obj.close()
        except Exception:
            logger.warning("Failed to close %r", obj, exc_info=True)


def _teardown_cluster(dask_cluster, gateway):
    """Best-effort shutdown of a gateway Dask cluster. Never raises.

    Runs in a finally (#144): a failed job used to skip shutdown entirely,
    leaking scheduler + worker pods until CLUSTER_IDLE_TIMEOUT. Swallows all
    errors so teardown can't mask the job's real exception.
    """
    if not dask_cluster:
        return
    try:
        # Can't assume the same cluster is running post process graph execution
        # due to sub workflows processing. If it was closed, check for a new one!
        if dask_cluster.status == "closed" and gateway is not None:
            cluster_list = gateway.list_clusters()
            if cluster_list:
                dask_cluster = gateway.connect(cluster_list[0].name)

        # Can call shutdown on previously closed clusters.
        dask_cluster.shutdown()
    except Exception:
        logger.warning("Failed to shut down Dask gateway cluster", exc_info=True)


def _collect_result_files(results_path: str) -> list[str]:
    """Return all non-hidden files below the executor results directory."""
    import fsspec

    fs = fsspec.filesystem(protocol="file")
    return [
        f
        for f in fs.find(results_path)
        if not Path(f).name.startswith(".") and fs.isfile(f)
    ]


def _find_stac_collections(results_path: str) -> list[Path]:
    """Return package-generated STAC collection JSONs below results_path."""
    import json

    collections = []
    for candidate in sorted(Path(results_path).rglob("*.json")):
        if any(part.startswith(".") for part in candidate.relative_to(results_path).parts):
            continue
        try:
            with open(candidate) as f:
                payload = json.load(f)
        except Exception:
            continue
        if payload.get("type") == "Collection":
            collections.append(candidate)
    return collections


def _publish_stac_collection(
    collection_file: Path,
    stac_path: str,
    job_id: str,
    stac_api_url: str,
    post_json_func,
) -> None:
    """Normalize, upload assets for, and publish a package-generated STAC collection."""
    import json
    import shutil

    from openeo_argoworkflows_executor.extra_processes.process_implementations.s3 import (
        upload_stac_item_assets,
    )

    stac_dir = Path(stac_path)
    stac_dir.mkdir(parents=True, exist_ok=True)
    items_dir = stac_dir / "items"
    if items_dir.exists():
        shutil.rmtree(items_dir)
    items_dir.mkdir(parents=True, exist_ok=True)

    source_items_dir = collection_file.parent / "items"
    if source_items_dir.exists():
        for item_file in sorted(source_items_dir.glob("*.json")):
            shutil.copy2(item_file, items_dir / item_file.name)

    with open(collection_file) as f:
        collection = json.load(f)

    collection["id"] = job_id
    collection["links"] = [
        link
        for link in collection.get("links", [])
        if link.get("rel") not in {"self", "root", "parent", "items", "item"}
    ]
    collection["links"].append(
        {"rel": "items", "href": f"{stac_api_url.rstrip('/')}/{job_id}/items"}
    )

    collection_target = stac_dir / f"{job_id}.json"
    with open(collection_target, "w") as f:
        json.dump(collection, f, indent=2)

    n_uploaded = upload_stac_item_assets(items_dir)
    if n_uploaded:
        logger.info("Uploaded %s package-generated STAC asset(s) to S3", n_uploaded)

    post_json_func(stac_api_url, collection)
    for item_file in sorted(items_dir.glob("*.json")):
        with open(item_file) as f:
            item = json.load(f)
        post_json_func(f"{stac_api_url.rstrip('/')}/{job_id}/items", item)


@click.command()
@click.option(
    "--process_graph",
    type=str,
    required=True,
    help="OpenEO Process Graph as a JSON string.",
)
@click.option(
    "--user_profile",
    type=str,
    required=True,
    help="Profile of the Dask Cluster to initialise.",
)
@click.option(
    "--dask_profile",
    type=str,
    required=True,
    help="Profile of the Dask Cluster to initialise.",
)
def execute(process_graph, user_profile, dask_profile):
    """CLI for running the OpenEOExecutor on an OpenEO process graph."""

    import json
    import os

    import openeo_processes_dask
    from dask_gateway import Gateway
    from openeo_argoworkflows_executor.executor import _is_cwl_job, execute
    from openeo_argoworkflows_executor.models import ExecutorParameters
    from openeo_pg_parser_networkx.graph import OpenEOProcessGraph

    logger.info(
        f"Using processes from openeo-processes-dask v{openeo_processes_dask.__version__}"
    )

    openeo_parameters = ExecutorParameters(
        process_graph=json.loads(process_graph),
        user_profile=json.loads(user_profile),
        dask_profile=json.loads(dask_profile),
    )

    if not openeo_parameters.user_profile.OPENEO_USER_WORKSPACE.exists():
        openeo_parameters.user_profile.OPENEO_USER_WORKSPACE.mkdir(
            parents=True, exist_ok=True
        )

    if not openeo_parameters.user_profile.results_path.exists():
        openeo_parameters.user_profile.results_path.mkdir(parents=True, exist_ok=True)

    if not openeo_parameters.user_profile.stac_path.exists():
        openeo_parameters.user_profile.stac_path.mkdir(parents=True, exist_ok=True)

    os.environ["OPENEO_USER_WORKSPACE"] = str(
        openeo_parameters.user_profile.OPENEO_USER_WORKSPACE
    )
    os.environ["OPENEO_STAC_PATH"] = str(openeo_parameters.user_profile.stac_path)
    os.environ["OPENEO_RESULTS_PATH"] = str(openeo_parameters.user_profile.results_path)
    os.environ["OPENEO_USER_ID"] = str(openeo_parameters.user_profile.OPENEO_USER_ID)
    os.environ["OPENEO_JOB_ID"] = str(openeo_parameters.user_profile.OPENEO_JOB_ID)

    for s3_var in ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        val = getattr(openeo_parameters.user_profile, s3_var, None)
        if val:
            os.environ[s3_var] = val

    is_cwl = _is_cwl_job(openeo_parameters.process_graph)

    # CWL jobs don't need a Dask cluster — skip cluster setup entirely
    dask_cluster = None
    gateway = None
    client = None
    local_cluster = None
    if is_cwl:
        logger.info("CWL job detected — skipping Dask cluster setup")
    elif openeo_parameters.dask_profile.LOCAL:
        # Gateway-less: run a Dask cluster inside the executor pod. worker_client()
        # only works from inside an existing worker, so create a real LocalCluster
        # sized from the profile and connect a Client (which becomes the default
        # for the compute below). dask_cluster stays None so the gateway-specific
        # teardown (which references `gateway`) is skipped.
        from dask.distributed import Client, LocalCluster

        # processes=False -> single in-process (threaded) worker. The executor CLI
        # runs at import time without a `if __name__ == '__main__'` guard, so a
        # Nanny/spawn worker fails with "attempt to start a new process before the
        # current process has finished its bootstrapping phase". Threaded avoids it.
        local_cluster = LocalCluster(
            n_workers=1,
            processes=False,
            threads_per_worker=int(openeo_parameters.dask_profile.WORKER_CORES),
            memory_limit=f"{int(openeo_parameters.dask_profile.WORKER_MEMORY)}GiB",
        )
        client = Client(local_cluster)
    else:
        gateway = Gateway(openeo_parameters.dask_profile.GATEWAY_URL)
        options = gateway.cluster_options()

        options.OPENEO_JOB_ID = openeo_parameters.user_profile.OPENEO_JOB_ID
        options.OPENEO_USER_ID = openeo_parameters.user_profile.OPENEO_USER_ID

        options.IMAGE = openeo_parameters.dask_profile.OPENEO_EXECUTOR_IMAGE

        options.WORKER_CORES = int(openeo_parameters.dask_profile.WORKER_CORES)
        options.WORKER_MEMORY = int(openeo_parameters.dask_profile.WORKER_MEMORY)
        options.CLUSTER_IDLE_TIMEOUT = int(
            openeo_parameters.dask_profile.CLUSTER_IDLE_TIMEOUT
        )

        dask_cluster = gateway.new_cluster(options, shutdown_on_close=True)

        # We need to initiate a cluster with at least one worker, otherwise .scatter that's used in xgboost will timeout waiting for workers
        # See https://github.com/dask/distributed/issues/2941
        dask_cluster.adapt(
            minimum=1, maximum=int(openeo_parameters.dask_profile.WORKER_LIMIT)
        )
        client = dask_cluster.get_client()

    parsed_graph = OpenEOProcessGraph(pg_data=openeo_parameters.process_graph)
    is_cwl = _is_cwl_job(parsed_graph.pg_data)

    try:
        execute(parsed_graph=parsed_graph)
    finally:
        # Shut the gateway cluster down even when the job fails — otherwise the
        # scheduler + worker pods leak until CLUSTER_IDLE_TIMEOUT (#144) — and
        # close the client/gateway so their threads can't wedge shutdown (#147).
        _teardown_cluster(dask_cluster, gateway)
        _close_dask(client, gateway, local_cluster)

    from openeo_argoworkflows_executor.http_utils import post_json

    job_id = openeo_parameters.user_profile.OPENEO_JOB_ID
    results_path = str(openeo_parameters.user_profile.results_path)
    stac_path = str(openeo_parameters.user_profile.stac_path)
    stac_api_url = os.environ.get(
        "OPENEO_RESULTS_STAC_URL", "https://stac.openeo.eurac.edu/"
    )

    # Some save_result backends write a directory containing data plus STAC
    # metadata, so recurse and ignore directories.
    all_result_files = _collect_result_files(results_path)
    result_files = [f for f in all_result_files if f.endswith(".nc")]
    other_files = [f for f in all_result_files if not f.endswith(".nc")]
    package_stac_collections = _find_stac_collections(results_path)

    if package_stac_collections and not is_cwl:
        try:
            for collection_file in package_stac_collections:
                _publish_stac_collection(
                    collection_file=collection_file,
                    stac_path=stac_path,
                    job_id=job_id,
                    stac_api_url=stac_api_url,
                    post_json_func=post_json,
                )
        except Exception as e:
            logger.warning(
                "Package-generated STAC publishing failed for job %s: %s",
                job_id,
                e,
            )

    if result_files and not is_cwl and not package_stac_collections:
        logger.warning(
            "Found result NetCDF files for job %s without package-generated STAC. "
            "Direct STAC generation in argoworkflows is disabled; "
            "save_result outputs must be produced through openeo-processes-save-result.",
            job_id,
        )

    # Handle non-NetCDF output files (e.g. from CWL workflows)
    # Create minimal STAC collection and items so they appear in job results
    if other_files and not result_files and not package_stac_collections:
        try:
            from openeo_argoworkflows_executor.stac_cwl import create_cwl_stac

            create_cwl_stac(
                job_id=job_id,
                result_files=other_files,
                stac_path=stac_path,
                stac_api_url=stac_api_url,
            )
        except Exception as e:
            logger.warning(
                f"CWL STAC publishing failed for job {job_id}, results are still available: {e}"
            )

    # All work is done and on disk. Exit WITHOUT running interpreter shutdown:
    # lingering dask/aiohttp finalizer threads deadlock atexit (observed: 68
    # parked threads, job stuck in "running" forever, #147). Failure paths are
    # untouched — exceptions still propagate to click for a nonzero exit.
    import sys

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


cli.add_command(execute)

if __name__ == "__main__":
    cli()
