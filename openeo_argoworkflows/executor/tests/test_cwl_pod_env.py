"""Tests for CDSE credential injection into Calrissian pod-env-vars — issue #105.

TDD: tests written before/alongside implementation.

When AWS_* env vars are present in the executor pod's environment,
run_cwl must write a pod-env-vars.json and pass --pod-env-vars to Calrissian.
When they are absent, Calrissian must still be invoked (with a warning logged).
"""

import importlib.util
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_cwl_spec = importlib.util.spec_from_file_location(
    "cwl",
    Path(__file__).parent.parent
    / "openeo_argoworkflows_executor/extra_processes/process_implementations/cwl.py",
)
_cwl = importlib.util.module_from_spec(_cwl_spec)
_cwl_spec.loader.exec_module(_cwl)


_CDSE_VARS = {
    "AWS_ACCESS_KEY_ID": "TESTKEY123",
    "AWS_SECRET_ACCESS_KEY": "TESTSECRET456",
    "AWS_ENDPOINT_URL_S3": "https://eodata.dataspace.copernicus.eu",
}


def _captured_calrissian_args(monkeypatch, env_override: dict):
    """Run run_cwl with a fake CWL file and capture sys.argv + pod-env-vars content."""
    captured = {}

    def fake_calrissian_main():
        import sys as _sys
        argv = list(_sys.argv)
        captured["argv"] = argv
        # Read pod-env-vars file immediately — tempdir will be gone after run_cwl returns
        if "--pod-env-vars" in argv:
            pod_env_file = Path(argv[argv.index("--pod-env-vars") + 1])
            if pod_env_file.exists():
                captured["pod_env"] = json.loads(pod_env_file.read_text())
        return 0

    with tempfile.TemporaryDirectory() as tmpdir:
        cwl_content = json.dumps({
            "class": "CommandLineTool",
            "cwlVersion": "v1.0",
            "baseCommand": "echo",
            "inputs": {},
            "outputs": {},
        })

        monkeypatch.setenv("OPENEO_RESULTS_PATH", tmpdir)
        monkeypatch.setenv("OPENEO_USER_WORKSPACE", tmpdir)
        for k, v in env_override.items():
            monkeypatch.setenv(k, v)
        for k in _CDSE_VARS:
            if k not in env_override:
                monkeypatch.delenv(k, raising=False)

        with patch.object(_cwl, "_validate_cwl", return_value={"valid": True, "errors": []}), \
             patch.object(_cwl, "_patch_calrissian_container_lookup"), \
             patch("calrissian.main.main", fake_calrissian_main):
            try:
                _cwl.run_cwl(cwl=cwl_content, inputs={})
            except Exception:
                pass  # post-run STAC processing may fail; we only care about args

    return captured


class TestCdseCredentialInjection:

    def test_pod_env_vars_arg_present_when_credentials_set(self, monkeypatch):
        result = _captured_calrissian_args(monkeypatch, _CDSE_VARS)
        assert "--pod-env-vars" in result["argv"]

    def test_pod_env_vars_file_contains_all_three_keys(self, monkeypatch):
        result = _captured_calrissian_args(monkeypatch, _CDSE_VARS)
        data = result.get("pod_env", {})
        for k in _CDSE_VARS:
            assert k in data, f"{k} missing from pod-env-vars.json"
            assert data[k] == _CDSE_VARS[k]

    def test_no_pod_env_vars_arg_when_no_credentials(self, monkeypatch):
        result = _captured_calrissian_args(monkeypatch, {})
        assert "--pod-env-vars" not in result.get("argv", [])

    def test_calrissian_still_invoked_without_credentials(self, monkeypatch):
        """Missing CDSE creds must not prevent Calrissian from running."""
        result = _captured_calrissian_args(monkeypatch, {})
        assert "calrissian" in result.get("argv", [])

    def test_partial_credentials_not_injected(self, monkeypatch):
        """Only inject when all three vars are present — partial set is skipped."""
        result = _captured_calrissian_args(monkeypatch, {"AWS_ACCESS_KEY_ID": "KEY"})
        assert "--pod-env-vars" not in result.get("argv", [])
