import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

__all__ = ["run_cwl", "run_udf"]

logger = logging.getLogger(__name__)

# Default resource limits for Calrissian
# 16G covers sar_slc_preprocessing (ramMin: 13000) and coherence/interferogram (7G/pair)
DEFAULT_MAX_RAM = "16G"
DEFAULT_MAX_CORES = 8


def _is_url(value: str) -> bool:
    """Check if a string looks like a URL."""
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https")


def _is_graph_cwl(cwl_path: Path) -> bool:
    """Return True if the CWL file is a $graph multi-tool package.

    A $graph document bundles multiple tools; Calrissian needs the '#main'
    entry point suffix to identify which tool to run.
    """
    try:
        text = cwl_path.read_text()
        if not text.strip():
            return False
        # Fast string check first — avoids full parse on plain tools
        if "$graph" not in text:
            return False
        # Confirm it's a top-level key (works for both JSON and YAML)
        import yaml
        doc = yaml.safe_load(text)
        return isinstance(doc, dict) and "$graph" in doc
    except Exception:
        return False


def _resolve_cwl_arg(cwl_path: Path) -> str:
    """Return the Calrissian CWL argument for the given file.

    Appends '#main' for $graph packages so Calrissian knows the entry point.
    """
    if _is_graph_cwl(cwl_path):
        return str(cwl_path) + "#main"
    return str(cwl_path)


def _resolve_cwl(cwl: str, work_dir: Path) -> Path:
    """Resolve a CWL argument to a local file path.

    If cwl is a URL, download it. If it's an inline string, write it to a
    temporary file. Returns the path to the CWL file on disk.
    """
    if _is_url(cwl):
        import urllib.request

        cwl_path = work_dir / "workflow.cwl"
        urllib.request.urlretrieve(cwl, str(cwl_path))
        logger.info(f"Downloaded CWL from {cwl} to {cwl_path}")
        return cwl_path

    cwl_path = work_dir / "workflow.cwl"
    cwl_path.write_text(cwl)
    logger.info(f"Wrote inline CWL to {cwl_path}")
    return cwl_path


def _write_inputs(inputs: dict, work_dir: Path) -> Path:
    """Write CWL job inputs to a JSON file."""
    inputs_path = work_dir / "inputs.json"
    inputs_path.write_text(json.dumps(inputs, indent=2))
    return inputs_path


def _validate_cwl(cwl_path: Path) -> dict:
    """Validate a CWL document using cwltool.

    Returns a dict with 'valid' (bool) and 'errors' (list of str).
    """
    result = subprocess.run(
        ["cwltool", "--validate", str(cwl_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode == 0:
        return {"valid": True, "errors": []}

    errors = []
    for line in (result.stderr or "").strip().splitlines():
        if line.strip():
            errors.append(line.strip())

    return {"valid": False, "errors": errors}

def _find_stac_root(directory: Path) -> Optional[Path]:
    """Search for a STAC root file in the calrissian output directory.

    Checks the top level first, then one level into subdirectories to handle
    CWL Directory-type outputs (e.g. s1-workflows tools output Directory).
    """
    for name in ("catalog.json", "catalogue.json", "collection.json"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    for subdir in sorted(directory.iterdir()):
        if subdir.is_dir():
            for name in ("catalog.json", "catalogue.json", "collection.json"):
                candidate = subdir / name
                if candidate.exists():
                    return candidate
    return None

def _collect_calrissian_outputs(calrissian_outdir: Path, results_path: Path) -> list:
    """Copy Calrissian output files to the openEO results directory.

    Returns a list of destination file paths.
    """
    collected = []
    if not calrissian_outdir.exists():
        return collected

    for item in calrissian_outdir.iterdir():
        if item.is_file():
            dest = results_path / item.name
            shutil.copy2(str(item), str(dest))
            collected.append(str(dest))
            logger.info(f"Collected CWL output: {item.name} -> {dest}")
        elif item.is_dir():
            dest_dir = results_path / item.name
            shutil.copytree(str(item), str(dest_dir))
            for f in dest_dir.rglob("*"):
                if f.is_file():
                    collected.append(str(f))
            logger.info(f"Collected CWL output directory: {item.name} -> {dest_dir}")

    return collected


def _patch_calrissian_container_lookup():
    """Patch Calrissian to use the 'main' container for PVC volume inspection.

    Calrissian's KubernetesPodVolumeInspector.get_first_container() returns
    pod.spec.containers[0], which in Argo Workflow pods is the 'wait'
    sidecar — not the executor. The sidecar mounts the workspace PVC at
    /mainctrfs/user_workspaces while the 'main' container mounts at
    /user_workspaces. This patch makes Calrissian find the 'main' container
    so PVC paths resolve correctly.

    Must be called in-process BEFORE calrissian.main.main().
    Does NOT work with subprocess invocation (separate Python interpreter).
    """
    try:
        from calrissian.job import KubernetesPodVolumeInspector

        _orig = KubernetesPodVolumeInspector.get_first_container

        def _get_main_container(self):
            for c in self.pod.spec.containers:
                if c.name == "main":
                    return c
            return _orig(self)

        KubernetesPodVolumeInspector.get_first_container = _get_main_container
        logger.info("Patched Calrissian to use 'main' container for PVC resolution")
    except Exception as e:
        logger.warning(f"Could not patch Calrissian container lookup: {e}")


def run_cwl(
    cwl: str,
    inputs: dict,
    options: Optional[dict] = None,
    **kwargs,
):
    """Execute a CWL workflow via Calrissian on Kubernetes.

    This executor-side implementation overrides the processes-dask stub.
    It resolves the CWL document, validates it, then invokes Calrissian
    in-process to run the workflow on the cluster.

    Calrissian is called in-process (not as a subprocess) so that the
    monkey-patch to KubernetesPodVolumeInspector takes effect. This is
    necessary because Argo Workflow pods have a 'wait' sidecar as
    containers[0], which Calrissian would otherwise inspect for PVC mounts.
    """
    if options is None:
        options = {}

    validate_only = options.get("validate_only", False)
    max_ram = options.get("max_ram", DEFAULT_MAX_RAM)
    max_cores = options.get("max_cores", DEFAULT_MAX_CORES)

    results_path = os.environ.get("OPENEO_RESULTS_PATH", "/tmp/results")
    os.makedirs(results_path, exist_ok=True)

    # Calrissian requires working directories to be on a PVC (not emptyDir).
    # Use the workspace PVC path for CWL working dirs.
    workspace_root = os.environ.get("OPENEO_USER_WORKSPACE", results_path)
    cwl_work_base = Path(workspace_root) / "_cwl_work"
    cwl_work_base.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="openeo_cwl_", dir=str(cwl_work_base)
    ) as work_dir:
        work_dir = Path(work_dir)

        # Resolve CWL to a local file
        try:
            cwl_path = _resolve_cwl(cwl, work_dir)
        except Exception as e:
            raise RuntimeError(f"Failed to resolve CWL document: {e}")

        # Validate CWL
        validation = _validate_cwl(cwl_path)

        if not validation["valid"]:
            raise RuntimeError(
                f"CWL validation failed: {'; '.join(validation['errors'])}"
            )

        if validate_only:
            return validation

        # Write inputs file
        inputs_path = _write_inputs(inputs, work_dir)

        # Set up Calrissian output and tmp dirs on the PVC
        calrissian_outdir = work_dir / "output"
        calrissian_outdir.mkdir(exist_ok=True)

        calrissian_tmpdir = work_dir / "tmp"
        calrissian_tmpdir.mkdir(exist_ok=True)

        # Calrissian requires CALRISSIAN_POD_NAME to identify itself in K8s
        if "CALRISSIAN_POD_NAME" not in os.environ:
            import socket

            os.environ["CALRISSIAN_POD_NAME"] = socket.gethostname()
            logger.info(f"Set CALRISSIAN_POD_NAME={os.environ['CALRISSIAN_POD_NAME']}")

        # Patch Calrissian to look at the 'main' container (not the Argo
        # 'wait' sidecar at containers[0]) for PVC volume mounts.
        # This works because we call Calrissian in-process below.
        _patch_calrissian_container_lookup()

        # Build Calrissian CLI args. We call calrissian.main.main()
        # in-process (not subprocess) so the monkey-patch above takes
        # effect. Calrissian uses argparse on sys.argv.
        calrissian_args = [
            "calrissian",
            "--max-ram",
            str(max_ram),
            "--max-cores",
            str(max_cores),
            "--outdir",
            str(calrissian_outdir),
            "--tmp-outdir-prefix",
            str(calrissian_tmpdir) + "/",
            "--stdout",
            str(work_dir / "cwl-stdout.json"),
            "--stderr",
            str(work_dir / "cwl-stderr.log"),
            _resolve_cwl_arg(cwl_path),
            str(inputs_path),
        ]

        logger.info(f"Running Calrissian in-process: {' '.join(calrissian_args)}")

        # Save and replace sys.argv for Calrissian's argparse
        orig_argv = sys.argv
        return_code = 1
        try:
            sys.argv = calrissian_args
            from calrissian.main import main as calrissian_main

            return_code = calrissian_main() or 0
        except SystemExit as e:
            # Calrissian/argparse may call sys.exit()
            return_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        except Exception as e:
            raise RuntimeError(f"Calrissian execution error: {e}")
        finally:
            sys.argv = orig_argv

        if return_code != 0:
            error_detail = "Unknown error"
            stderr_log = work_dir / "cwl-stderr.log"
            if stderr_log.exists():
                error_detail = stderr_log.read_text()
            raise RuntimeError(
                f"CWL execution failed (exit code {return_code}): {error_detail}"
            )

        # Read Calrissian output manifest
        stdout_json = work_dir / "cwl-stdout.json"
        cwl_outputs = {}
        if stdout_json.exists():
            try:
                cwl_outputs = json.loads(stdout_json.read_text())
                logger.info(f"CWL outputs: {json.dumps(cwl_outputs, indent=2)}")
            except json.JSONDecodeError:
                logger.warning("Could not parse CWL stdout as JSON")

        # Collect output files to RESULTS/
        stac_root = _find_stac_root(calrissian_outdir)
        if stac_root:
            job_id = os.environ.get("OPENEO_JOB_ID") or Path(workspace_root).name
            stac_path = Path(workspace_root) / "STAC"
            if stac_path.exists():
                shutil.rmtree(stac_path)
                
            shutil.copytree(str(calrissian_outdir), str(stac_path))
            # Rename root to {job_id}.json as expected by the API
            copied_root = stac_path / stac_root.name
            if copied_root.exists() and copied_root.name != f"{job_id}.json":
                copied_root.rename(stac_path / f"{job_id}.json")

            # Move STAC Item files (type==Feature) from STAC/ root into items/ subdir
            items_dir = stac_path / "items"
            items_dir.mkdir(exist_ok=True)
            collection_file = stac_path / f"{job_id}.json"
            for candidate in list(stac_path.iterdir()):
                if candidate.suffix != ".json" or candidate == collection_file:
                    continue
                try:
                    with open(candidate) as f:
                        j = json.load(f)
                    if j.get("type") == "Feature":
                        shutil.move(str(candidate), str(items_dir / candidate.name))
                except Exception:
                    pass

            # Rewrite relative asset hrefs to absolute paths in moved item files
            for item_file in items_dir.iterdir():
                if item_file.suffix != ".json":
                    continue
                with open(item_file) as f:
                    item_dict = json.load(f)
                changed = False
                for asset_val in item_dict.get("assets", {}).values():
                    href = asset_val.get("href", "")
                    if not href.startswith("/"):
                        abs_path = stac_path / href.lstrip("./")
                        if abs_path.exists():
                            asset_val["href"] = str(abs_path)
                            changed = True
                if changed:
                    with open(item_file, "w") as f:
                        json.dump(item_dict, f, indent=2)

            logger.info(f"CWL produced STAC root ({stac_root.name}) - restructured to {stac_path}")
            collected_files = [str(f) for f in stac_path.rglob("*") if f.is_file()]
        else:
            # No STAC root -  flat file copy + generate STAC via stac_cwl.py
            collected_files = _collect_calrissian_outputs(
                calrissian_outdir, Path(results_path)
            )
            logger.info(f"Collected {len(collected_files)} output files to {results_path}")
        # Return output metadata for downstream processing
        return {
            "cwl_outputs": cwl_outputs,
            "collected_files": collected_files,
            "status": "completed",
        }

def run_udf(
    data=None,
    udf: str = "",
    runtime: str = "",
    version: Optional[str] = None,
    context: Optional[dict] = None,
    **kwargs,
):
    """run_udf handler for EOAP-CWL runtime.

    Maps run_udf parameters to run_cwl:
      udf     -> cwl  (the CWL document or URL)
      context -> inputs (CWL input key-value pairs)

    If `data` is a file path string (returned by save_result), it is
    injected into CWL inputs as `openeo_data` so CWL tools can reference
    the staged file without needing an unresolvable from_node in context.
    """
    if runtime.lower() != "eoap-cwl":
        raise RuntimeError(
            f"Unsupported runtime '{runtime}'. This backend only supports 'EOAP-CWL'."
        )

    inputs = dict(context or {})
    if isinstance(data, str) and data.startswith("/"):
        # Pass as CWL File object so Calrissian stages it into the tool pod's
        # working directory. A plain string path won't work because CWL tool
        # containers only have the working-dir PVC mount, not /user_workspaces.
        inputs.setdefault("openeo_data", {"class": "File", "location": f"file://{data}"})
        logger.info(f"Injecting staged data as CWL File input (openeo_data): {data}")
    elif data is not None:
        # Standalone CWL tool — data may be an xarray object from a preceding
        # process or a non-path string. Ignore it; use context for CWL inputs.
        logger.info(f"Ignoring non-path data argument (type={type(data).__name__}); using context inputs only")

    return run_cwl(
        cwl=udf,
        inputs=inputs,
        **kwargs,
    )
