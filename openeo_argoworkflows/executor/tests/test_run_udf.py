"""Tests for run_udf — issue #104.

TDD: tests written before implementation.

Scenarios:
  - data=None: standalone CWL tool, openeo_data NOT injected
  - data="/path/to/file": from save_result, injected as CWL File object
  - data=<xarray/dict/other>: non-path data (e.g. direct from load_collection),
    must NOT crash — treated the same as None (no injection)
  - wrong runtime: must raise RuntimeError immediately
"""

import importlib.util
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
run_udf = _cwl.run_udf


class TestRunUdf:

    def _mock_run_cwl(self, mocker=None):
        """Patch run_cwl on the cwl module and return the mock."""
        return patch.object(_cwl, "run_cwl", return_value={"status": "completed"})

    def test_rejects_unsupported_runtime(self):
        with pytest.raises(RuntimeError, match="Unsupported runtime"):
            run_udf(data=None, udf="workflow.cwl", runtime="python")

    def _get_inputs(self, mock_cwl) -> dict:
        """Extract the 'inputs' kwarg from a patched run_cwl call."""
        return mock_cwl.call_args.kwargs["inputs"]

    def test_data_none_does_not_inject_openeo_data(self):
        with self._mock_run_cwl() as mock_cwl:
            run_udf(data=None, udf="workflow.cwl", runtime="eoap-cwl", context={"key": "val"})
            assert "openeo_data" not in self._get_inputs(mock_cwl)

    def test_data_file_path_injects_openeo_data(self):
        with self._mock_run_cwl() as mock_cwl:
            run_udf(
                data="/user_workspaces/user123/results/output.nc",
                udf="workflow.cwl",
                runtime="eoap-cwl",
                context={},
            )
            inputs = self._get_inputs(mock_cwl)
            assert "openeo_data" in inputs
            assert inputs["openeo_data"]["class"] == "File"
            assert "file:///user_workspaces/user123/results/output.nc" in inputs["openeo_data"]["location"]

    def test_data_non_path_string_does_not_inject(self):
        """A relative string or non-path value must not be treated as a file."""
        with self._mock_run_cwl() as mock_cwl:
            run_udf(data="some-string-value", udf="workflow.cwl", runtime="eoap-cwl", context={})
            assert "openeo_data" not in self._get_inputs(mock_cwl)

    def test_data_xarray_like_does_not_crash(self):
        """Non-string data (e.g. xarray DataArray from load_collection) must be ignored."""
        fake_xarray = MagicMock()
        fake_xarray.__str__ = lambda self: "<DataArray>"
        with self._mock_run_cwl() as mock_cwl:
            run_udf(data=fake_xarray, udf="workflow.cwl", runtime="eoap-cwl", context={})
            assert "openeo_data" not in self._get_inputs(mock_cwl)

    def test_data_dict_does_not_crash(self):
        """dict data (xr.Dataset-like) must be silently ignored."""
        with self._mock_run_cwl() as mock_cwl:
            run_udf(data={"variable": "B04"}, udf="workflow.cwl", runtime="eoap-cwl", context={})
            assert "openeo_data" not in self._get_inputs(mock_cwl)

    def test_context_inputs_passed_through(self):
        """context dict is always forwarded to run_cwl as base inputs."""
        with self._mock_run_cwl() as mock_cwl:
            run_udf(
                data=None,
                udf="workflow.cwl",
                runtime="eoap-cwl",
                context={"date_range": ["2022-01-01", "2022-12-31"], "aoi": "POLYGON(...)"},
            )
            inputs = self._get_inputs(mock_cwl)
            assert inputs["date_range"] == ["2022-01-01", "2022-12-31"]
            assert inputs["aoi"] == "POLYGON(...)"

    def test_runtime_check_is_case_insensitive(self):
        """EOAP-CWL, eoap-cwl, Eoap-Cwl should all be accepted."""
        with self._mock_run_cwl() as mock_cwl:
            run_udf(data=None, udf="workflow.cwl", runtime="EOAP-CWL", context={})
            run_udf(data=None, udf="workflow.cwl", runtime="Eoap-Cwl", context={})
        # No exception raised means pass
