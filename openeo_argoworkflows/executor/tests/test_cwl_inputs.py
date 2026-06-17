"""Tests for issue #127 — dynamic CWL context generation from the CWL inputs block.

TDD: tests written before implementation.

Covers the pure helpers added to cwl.py:
  - _load_cwl_doc        : YAML-load a CWL file, resolving $graph to the #main tool
  - _parse_cwl_inputs    : normalise the CWL `inputs:` block (mapping or list form)
  - _auto_wire_inputs    : fill declared inputs from openEO env (job_id, user_id)
  - _missing_required_inputs : list required-and-unprovided inputs (for the warning)

The module is loaded standalone (like test_run_udf.py) to avoid pulling in the
executor's heavy dependency tree.
"""

import importlib.util
import os
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


class TestParseCwlInputs:
    """Normalising the CWL inputs block into {name: {type, optional, required, ...}}."""

    def test_mapping_form_simple_string(self):
        parsed = _cwl._parse_cwl_inputs({"inputs": {"message": "string"}})
        assert "message" in parsed
        assert parsed["message"]["required"] is True
        assert parsed["message"]["optional"] is False

    def test_mapping_form_with_default_is_not_required(self):
        parsed = _cwl._parse_cwl_inputs(
            {"inputs": {"count": {"type": "int", "default": 5}}}
        )
        assert parsed["count"]["has_default"] is True
        assert parsed["count"]["required"] is False

    def test_optional_via_question_mark(self):
        parsed = _cwl._parse_cwl_inputs({"inputs": {"opt": "string?"}})
        assert parsed["opt"]["optional"] is True
        assert parsed["opt"]["required"] is False

    def test_optional_via_null_union(self):
        parsed = _cwl._parse_cwl_inputs(
            {"inputs": {"nullable": {"type": ["null", "string"]}}}
        )
        assert parsed["nullable"]["optional"] is True
        assert parsed["nullable"]["required"] is False

    def test_list_form(self):
        parsed = _cwl._parse_cwl_inputs(
            {
                "inputs": [
                    {"id": "message", "type": "string"},
                    {"id": "count", "type": "int", "default": 5},
                ]
            }
        )
        assert parsed["message"]["required"] is True
        assert parsed["count"]["required"] is False

    def test_missing_inputs_block_returns_empty(self):
        assert _cwl._parse_cwl_inputs({"class": "CommandLineTool"}) == {}
        assert _cwl._parse_cwl_inputs({}) == {}


class TestLoadCwlDoc:
    """Loading a CWL file, including $graph packages."""

    def _write(self, tmp_path, text):
        p = tmp_path / "workflow.cwl"
        p.write_text(text)
        return p

    def test_plain_tool(self, tmp_path):
        p = self._write(
            tmp_path,
            "class: CommandLineTool\ncwlVersion: v1.2\ninputs:\n  msg: string\noutputs: {}\n",
        )
        doc = _cwl._load_cwl_doc(p)
        assert doc.get("class") == "CommandLineTool"
        assert "msg" in _cwl._parse_cwl_inputs(doc)

    def test_graph_resolves_main_entry(self, tmp_path):
        p = self._write(
            tmp_path,
            (
                "cwlVersion: v1.2\n"
                "$graph:\n"
                "  - id: helper\n"
                "    class: CommandLineTool\n"
                "    inputs:\n      helper_in: string\n    outputs: {}\n"
                "  - id: main\n"
                "    class: Workflow\n"
                "    inputs:\n      main_in: string\n    outputs: {}\n    steps: {}\n"
            ),
        )
        doc = _cwl._load_cwl_doc(p)
        parsed = _cwl._parse_cwl_inputs(doc)
        assert "main_in" in parsed
        assert "helper_in" not in parsed


class TestAutoWireInputs:
    """Filling declared inputs from openEO execution env — declaration-gated."""

    def test_wires_job_id_and_user_id_when_declared(self):
        cwl_inputs = _cwl._parse_cwl_inputs(
            {"inputs": {"job_id": "string", "user_id": "string"}}
        )
        with patch.dict(os.environ, {"OPENEO_JOB_ID": "job-abc", "OPENEO_USER_ID": "user-xyz"}):
            wired = _cwl._auto_wire_inputs(cwl_inputs, {})
        assert wired["job_id"] == "job-abc"
        assert wired["user_id"] == "user-xyz"

    def test_does_not_wire_input_not_declared_by_cwl(self):
        cwl_inputs = _cwl._parse_cwl_inputs({"inputs": {"message": "string"}})
        with patch.dict(os.environ, {"OPENEO_JOB_ID": "job-abc"}):
            wired = _cwl._auto_wire_inputs(cwl_inputs, {})
        assert "job_id" not in wired

    def test_user_supplied_value_wins(self):
        cwl_inputs = _cwl._parse_cwl_inputs({"inputs": {"job_id": "string"}})
        with patch.dict(os.environ, {"OPENEO_JOB_ID": "env-job"}):
            wired = _cwl._auto_wire_inputs(cwl_inputs, {"job_id": "user-job"})
        assert wired["job_id"] == "user-job"

    def test_skips_when_env_absent(self):
        cwl_inputs = _cwl._parse_cwl_inputs({"inputs": {"job_id": "string"}})
        with patch.dict(os.environ, {}, clear=True):
            wired = _cwl._auto_wire_inputs(cwl_inputs, {})
        assert "job_id" not in wired

    def test_does_not_mutate_caller_inputs(self):
        cwl_inputs = _cwl._parse_cwl_inputs({"inputs": {"job_id": "string"}})
        original = {}
        with patch.dict(os.environ, {"OPENEO_JOB_ID": "job-abc"}):
            _cwl._auto_wire_inputs(cwl_inputs, original)
        assert original == {}


class TestMissingRequiredInputs:
    """Listing required-and-unprovided inputs for the user-facing warning."""

    def test_lists_required_missing(self):
        cwl_inputs = _cwl._parse_cwl_inputs({"inputs": {"aoi": "string"}})
        assert _cwl._missing_required_inputs(cwl_inputs, {}) == ["aoi"]

    def test_default_input_not_listed(self):
        cwl_inputs = _cwl._parse_cwl_inputs(
            {"inputs": {"count": {"type": "int", "default": 1}}}
        )
        assert _cwl._missing_required_inputs(cwl_inputs, {}) == []

    def test_optional_input_not_listed(self):
        cwl_inputs = _cwl._parse_cwl_inputs({"inputs": {"opt": "string?"}})
        assert _cwl._missing_required_inputs(cwl_inputs, {}) == []

    def test_provided_input_not_listed(self):
        cwl_inputs = _cwl._parse_cwl_inputs({"inputs": {"aoi": "string"}})
        assert _cwl._missing_required_inputs(cwl_inputs, {"aoi": "POLYGON(...)"}) == []


class TestRunCwlAutoWiringIntegration:
    """Drive run_cwl() far enough to prove the parsing/auto-wiring is wired in.

    Validation is stubbed and execution is short-circuited at the _write_inputs
    boundary, where we capture the final inputs dict that would be handed to
    Calrissian.
    """

    _INLINE_CWL = (
        "class: CommandLineTool\n"
        "cwlVersion: v1.2\n"
        "baseCommand: echo\n"
        "inputs:\n  job_id: string\n"
        "outputs: {}\n"
    )

    def test_run_cwl_injects_declared_job_id_from_env(self, tmp_path, monkeypatch):
        captured = {}

        class _Stop(Exception):
            pass

        def _capture(inputs, work_dir):
            captured.update(inputs)
            raise _Stop()

        monkeypatch.setenv("OPENEO_RESULTS_PATH", str(tmp_path / "results"))
        monkeypatch.setenv("OPENEO_USER_WORKSPACE", str(tmp_path / "ws"))
        monkeypatch.setenv("OPENEO_JOB_ID", "job-int-123")
        monkeypatch.setattr(_cwl, "_validate_cwl", lambda p: {"valid": True, "errors": []})
        monkeypatch.setattr(_cwl, "_write_inputs", _capture)

        with pytest.raises(_Stop):
            _cwl.run_cwl(cwl=self._INLINE_CWL, inputs={})

        assert captured.get("job_id") == "job-int-123"
