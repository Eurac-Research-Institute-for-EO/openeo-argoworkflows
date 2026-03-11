import click
import fsspec
import logging

logger = logging.getLogger(__name__)

@click.group()
def cli():
    """Defining group for executor CLI."""
    pass

@click.command()
@click.option(
    '--process_graph',
    type=str,
    required=True,
    help='OpenEO Process Graph as a JSON string.',
)
@click.option(
    '--user_profile',
    type=str,
    required=True,
    help='Profile of the Dask Cluster to initialise.',
)
@click.option(
    '--dask_profile',
    type=str,
    required=True,
    help='Profile of the Dask Cluster to initialise.',
)
def execute(process_graph, user_profile, dask_profile):
    """CLI for running the OpenEOExecutor on an OpenEO process graph."""
    
    import os
    import json

    from dask_gateway import Gateway
    import openeo_processes_dask
    from openeo_pg_parser_networkx.graph import OpenEOProcessGraph

    from openeo_argoworkflows_executor.executor import execute, _is_cwl_job
    from openeo_argoworkflows_executor.models import ExecutorParameters
    from openeo_argoworkflows_executor.stac import create_stac_item

    logger.info(
        f"Using processes from openeo-processes-dask v{openeo_processes_dask.__version__}"
    )

    openeo_parameters = ExecutorParameters(
        process_graph=json.loads(process_graph),
        user_profile=json.loads(user_profile),
        dask_profile=json.loads(dask_profile)
    )

    if not openeo_parameters.user_profile.OPENEO_USER_WORKSPACE.exists():
        openeo_parameters.user_profile.OPENEO_USER_WORKSPACE.mkdir(parents=True, exist_ok=True)

    if not openeo_parameters.user_profile.results_path.exists():
        openeo_parameters.user_profile.results_path.mkdir(parents=True, exist_ok=True)

    if not openeo_parameters.user_profile.stac_path.exists():
        openeo_parameters.user_profile.stac_path.mkdir(parents=True, exist_ok=True)

    os.environ["OPENEO_USER_WORKSPACE"] = str(openeo_parameters.user_profile.OPENEO_USER_WORKSPACE)
    os.environ["OPENEO_STAC_PATH"] = str(openeo_parameters.user_profile.stac_path)
    os.environ["OPENEO_RESULTS_PATH"] = str(openeo_parameters.user_profile.results_path)

    is_cwl = _is_cwl_job(openeo_parameters.process_graph)

    # CWL jobs don't need a Dask cluster — skip cluster setup entirely
    dask_cluster = None
    if is_cwl:
        logger.info("CWL job detected — skipping Dask cluster setup")
    elif openeo_parameters.dask_profile.LOCAL:
        from dask.distributed import worker_client

        client = worker_client()
    else:
        gateway = Gateway(openeo_parameters.dask_profile.GATEWAY_URL)
        options = gateway.cluster_options()

        options.OPENEO_JOB_ID = openeo_parameters.user_profile.OPENEO_JOB_ID
        options.OPENEO_USER_ID = openeo_parameters.user_profile.OPENEO_USER_ID

        options.IMAGE = openeo_parameters.dask_profile.OPENEO_EXECUTOR_IMAGE

        options.WORKER_CORES = int(openeo_parameters.dask_profile.WORKER_CORES)
        options.WORKER_MEMORY = int(openeo_parameters.dask_profile.WORKER_MEMORY)
        options.CLUSTER_IDLE_TIMEOUT = int(openeo_parameters.dask_profile.CLUSTER_IDLE_TIMEOUT)

        dask_cluster = gateway.new_cluster(options, shutdown_on_close=True)

        # We need to initiate a cluster with at least one worker, otherwise .scatter that's used in xgboost will timeout waiting for workers
        # See https://github.com/dask/distributed/issues/2941
        dask_cluster.adapt(minimum=1, maximum=int(openeo_parameters.dask_profile.WORKER_LIMIT))
        client = dask_cluster.get_client()

    parsed_graph = OpenEOProcessGraph(pg_data=openeo_parameters.process_graph)

    execute(parsed_graph=parsed_graph)

    # Can't assume the same cluster is running post process graph execution due to sub workflows processing.
    # If the previous cluster was closed, check for a new one!
    if dask_cluster:
        if dask_cluster.status == 'closed':
            cluster_list = gateway.list_clusters()
            if cluster_list:
                dask_cluster = gateway.connect(cluster_list[0].name)

        # Can call shutdown on previously closed clusters.
        dask_cluster.shutdown()

    import json
    import requests
    import xarray as xr
    from raster2stac import Raster2STAC

    job_id = openeo_parameters.user_profile.OPENEO_JOB_ID
    results_path = str(openeo_parameters.user_profile.results_path)
    stac_path = str(openeo_parameters.user_profile.stac_path)
    stac_api_url = os.environ.get("OPENEO_RESULTS_STAC_URL", "https://stac.openeo.eurac.edu/")

    # Collect all result files
    fs = fsspec.filesystem(protocol="file")
    all_result_files = [f["name"] for f in fs.listdir(results_path) if not f["name"].startswith(".")]
    result_files = [f for f in all_result_files if f.endswith(".nc")]
    other_files = [f for f in all_result_files if not f.endswith(".nc")]

    if result_files:
        try:
            # Workaround for raster2stac bugs:
            # Bug 1: generate_netcdf_stac() fails with file paths on HDF5-based NetCDF4 files
            #        (xr.open_dataset() missing engine='netcdf4')
            # Bug 2: _ensure_crs() does not detect CRS from CF grid_mapping variable (spatial_ref)
            # Fix: open files explicitly and write CRS via rioxarray before passing datasets.
            datasets = []
            for filepath in result_files:
                ds = xr.open_dataset(filepath, engine='netcdf4')
                if ds.rio.crs is None:
                    # Read CRS from CF grid_mapping variable if present
                    # grid_mapping var may be in data_vars or coords
                    crs_wkt = None
                    for var in ds.data_vars:
                        gm = ds[var].attrs.get("grid_mapping")
                        if gm and gm in ds:
                            crs_wkt = ds[gm].attrs.get("crs_wkt") or ds[gm].attrs.get("spatial_ref")
                            if crs_wkt:
                                break
                    if crs_wkt is None and "spatial_ref" in ds:
                        crs_wkt = ds["spatial_ref"].attrs.get("crs_wkt") or ds["spatial_ref"].attrs.get("spatial_ref")
                    if crs_wkt:
                        ds = ds.rio.write_crs(crs_wkt)
                datasets.append(ds)

            # raster2stac calls item.validate() which fetches remote JSON schemas from
            # proj.org — blocked in the executor pod (403). Disable pystac validation.
            import pystac
            pystac.Item.validate = lambda self: []

            # raster2stac expects a double-nested list when passing xarray Datasets:
            # outer list = collection items, inner list = files for the same timestamp.
            Raster2STAC(
                data=[[ds] for ds in datasets],
                collection_id=job_id,
                description=f"openEO batch job results for job {job_id}",
                collection_url=stac_api_url,
                output_folder=stac_path,
                s3_upload=False,
            ).generate_netcdf_stac()

            for ds in datasets:
                ds.close()

            # POST collection to STAC API
            collection_file = f"{stac_path}/{job_id}.json"
            if os.path.exists(collection_file):
                with open(collection_file, "r") as f:
                    collection_dict = json.load(f)
                requests.post(stac_api_url, json=collection_dict)

            # POST each item to STAC API
            items_csv = f"{stac_path}/inline_items.csv"
            if os.path.exists(items_csv):
                with open(items_csv, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            item_dict = json.loads(line)
                            requests.post(f"{stac_api_url.rstrip('/')}/{job_id}/items", json=item_dict)

        except Exception as e:
            logger.warning(f"STAC publishing failed for job {job_id}, results are still available: {e}")

    # Handle non-NetCDF output files (e.g. from CWL workflows)
    # Create minimal STAC collection and items so they appear in job results
    if other_files and not result_files:
        try:
            from openeo_argoworkflows_executor.stac_cwl import create_cwl_stac

            create_cwl_stac(
                job_id=job_id,
                result_files=other_files,
                stac_path=stac_path,
                stac_api_url=stac_api_url,
            )
        except Exception as e:
            logger.warning(f"CWL STAC publishing failed for job {job_id}, results are still available: {e}")


cli.add_command(execute)

if __name__ == '__main__':
    cli()
