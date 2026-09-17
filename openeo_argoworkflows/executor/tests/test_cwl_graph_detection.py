"""Tests for $graph CWL detection — issue #103.

TDD: these tests were written BEFORE the implementation.
A $graph CWL package contains multiple tools; Calrissian needs the entry
point appended as `{cwl_path}#main` to know which tool to run.
"""

import importlib.util
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Import cwl.py directly to avoid __init__.py pulling in heavy deps
_cwl_spec = importlib.util.spec_from_file_location(
    "cwl",
    Path(__file__).parent.parent
    / "openeo_argoworkflows_executor/extra_processes/process_implementations/cwl.py",
)
_cwl = importlib.util.module_from_spec(_cwl_spec)
_cwl_spec.loader.exec_module(_cwl)

_is_graph_cwl = _cwl._is_graph_cwl
_resolve_cwl_arg = _cwl._resolve_cwl_arg


class TestIsGraphCwl:
    """Tests for _is_graph_cwl(cwl_path) — detects $graph CWL packages."""

    def _write_cwl(self, tmpdir, content: str) -> Path:
        p = Path(tmpdir) / "workflow.cwl"
        p.write_text(content)
        return p

    def test_detects_graph_cwl_with_dollar_graph_key(self):
        cwl_content = json.dumps({
            "$graph": [
                {"class": "CommandLineTool", "id": "ndvi"},
                {"class": "Workflow", "id": "main"},
            ],
            "cwlVersion": "v1.0",
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write_cwl(tmpdir, cwl_content)
            assert _is_graph_cwl(p) is True

    def test_detects_graph_cwl_yaml_style(self):
        cwl_content = """\
$graph:
  - class: Workflow
    id: main
cwlVersion: v1.0
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write_cwl(tmpdir, cwl_content)
            assert _is_graph_cwl(p) is True

    def test_returns_false_for_single_tool(self):
        cwl_content = json.dumps({
            "class": "CommandLineTool",
            "cwlVersion": "v1.0",
            "baseCommand": "echo",
            "inputs": {},
            "outputs": {},
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write_cwl(tmpdir, cwl_content)
            assert _is_graph_cwl(p) is False

    def test_returns_false_for_single_workflow(self):
        cwl_content = json.dumps({
            "class": "Workflow",
            "cwlVersion": "v1.0",
            "inputs": {},
            "outputs": {},
            "steps": [],
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write_cwl(tmpdir, cwl_content)
            assert _is_graph_cwl(p) is False

    def test_returns_false_for_empty_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "workflow.cwl"
            p.write_text("")
            assert _is_graph_cwl(p) is False

    def test_returns_false_for_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "workflow.cwl"
            p.write_text("{not valid json or yaml")
            # Should not raise — just return False
            assert _is_graph_cwl(p) is False


class TestResolveCwlArg:
    """Tests for _resolve_cwl_arg(cwl_path) — appends #main for $graph files."""

    def test_appends_main_for_graph_cwl(self):
        cwl_content = json.dumps({
            "$graph": [{"class": "Workflow", "id": "main"}],
            "cwlVersion": "v1.0",
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "workflow.cwl"
            p.write_text(cwl_content)
            result = _resolve_cwl_arg(p)
            assert result == str(p) + "#main"

    def test_no_suffix_for_plain_cwl(self):
        cwl_content = json.dumps({
            "class": "CommandLineTool",
            "cwlVersion": "v1.0",
            "baseCommand": "echo",
            "inputs": {},
            "outputs": {},
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "workflow.cwl"
            p.write_text(cwl_content)
            result = _resolve_cwl_arg(p)
            assert result == str(p)

    def test_no_double_main_if_already_present(self):
        """If somehow the file already had #main in it — should not double-append."""
        cwl_content = json.dumps({
            "$graph": [{"class": "Workflow", "id": "main"}],
            "cwlVersion": "v1.0",
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "workflow.cwl"
            p.write_text(cwl_content)
            result = _resolve_cwl_arg(p)
            assert result.count("#main") == 1
