"""Tests for STAC output copy/rename logic in run_cwl.

Regression test for Directory-type CWL outputs where collection.json
is inside a subdirectory of calrissian_outdir, not at the top level.

Without the fix, copied_root = stac_path / stac_root.name resolved to
STAC/collection.json (wrong) instead of STAC/cq9u6uys/collection.json (right),
causing the rename to silently fail and the API to 500 on job result download.
"""

import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_cwl_spec = importlib.util.spec_from_file_location(
    "cwl",
    Path(__file__).parent.parent
    / "openeo_argoworkflows_executor/extra_processes/process_implementations/cwl.py",
)
_cwl = importlib.util.module_from_spec(_cwl_spec)
_cwl_spec.loader.exec_module(_cwl)
_find_stac_root = _cwl._find_stac_root


def _make_calrissian_outdir_flat(base: Path) -> Path:
    """Simulate flat CWL output — collection.json at top level."""
    out = base / "output"
    out.mkdir()
    (out / "collection.json").write_text(json.dumps({"type": "Collection"}))
    (out / "result.tif").write_bytes(b"fake tiff")
    return out


def _make_calrissian_outdir_directory(base: Path) -> Path:
    """Simulate Directory-type CWL output — collection.json one level deep."""
    out = base / "output"
    out.mkdir()
    subdir = out / "cq9u6uys"
    subdir.mkdir()
    (subdir / "collection.json").write_text(json.dumps({"type": "Collection"}))
    (subdir / "result.tif").write_bytes(b"fake tiff")
    return out


class TestStacRootRename:

    def _run_stac_copy(self, calrissian_outdir: Path, workspace: Path, job_id: str):
        """Replicate the STAC copy/rename block from run_cwl."""
        stac_root = _find_stac_root(calrissian_outdir)
        assert stac_root is not None

        stac_path = workspace / "STAC"
        if stac_path.exists():
            shutil.rmtree(stac_path)
        shutil.copytree(str(calrissian_outdir), str(stac_path))

        copied_root = stac_path / stac_root.relative_to(calrissian_outdir)
        if copied_root.exists() and copied_root.name != f"{job_id}.json":
            copied_root.rename(stac_path / f"{job_id}.json")

        return stac_path

    def test_flat_output_renamed_to_job_id(self):
        """Top-level collection.json gets renamed to {job_id}.json."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outdir = _make_calrissian_outdir_flat(base)
            workspace = base / "workspace"
            workspace.mkdir()

            stac_path = self._run_stac_copy(outdir, workspace, "job-abc-123")

            assert (stac_path / "job-abc-123.json").exists()
            assert not (stac_path / "collection.json").exists()

    def test_directory_output_collection_renamed_to_job_id(self):
        """Collection inside subdirectory gets renamed to {job_id}.json at STAC root."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outdir = _make_calrissian_outdir_directory(base)
            workspace = base / "workspace"
            workspace.mkdir()

            stac_path = self._run_stac_copy(outdir, workspace, "job-xyz-456")

            assert (stac_path / "job-xyz-456.json").exists(), \
                "collection.json from subdirectory must be renamed to {job_id}.json at STAC root"

    def test_directory_output_tif_files_preserved(self):
        """GeoTIFF files inside the subdirectory must be preserved after rename."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outdir = _make_calrissian_outdir_directory(base)
            workspace = base / "workspace"
            workspace.mkdir()

            stac_path = self._run_stac_copy(outdir, workspace, "job-xyz-456")

            tifs = list(stac_path.rglob("*.tif"))
            assert len(tifs) == 1
