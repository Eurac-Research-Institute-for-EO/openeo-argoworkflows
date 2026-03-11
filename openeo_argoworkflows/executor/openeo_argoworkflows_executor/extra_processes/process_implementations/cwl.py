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

__all__ = ["run_cwl"]

logger = logging.getLogger(__name__)

# Default resource limits for Calrissian
DEFAULT_MAX_RAM = "8G"
DEFAULT_MAX_CORES = 4


def _is_url(value: str) -> bool:
    """Check if a string looks like a URL."""
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https")


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
            str(cwl_path),
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
