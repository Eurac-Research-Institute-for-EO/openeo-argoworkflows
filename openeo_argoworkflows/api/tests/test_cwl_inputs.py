"""Tests for #129 — API-side CWL input schema endpoint + parser.

TDD: written before the endpoint exists.

  - parser unit tests (mirror executor #127, plus the `autofilled` flag)
  - HTTP-level tests via TestClient against POST {OPENEO_PREFIX}/cwl/inputs
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from openeo_argoworkflows_api import cwl_inputs
from openeo_argoworkflows_api.app import app as app_api
from openeo_argoworkflows_api.app import client

PREFIX = client.settings.OPENEO_PREFIX
ENDPOINT = f"{PREFIX}/cwl/inputs"

_INLINE_CWL = (
    "cwlVersion: v1.2\n"
    "class: CommandLineTool\n"
    "baseCommand: echo\n"
    "inputs:\n"
    "  aoi: string\n"
    "  count:\n    type: int\n    default: 1\n"
    "  opt: string?\n"
    "  job_id: string\n"
    "outputs: {}\n"
)

_GRAPH_CWL = (
    "cwlVersion: v1.2\n"
    "$graph:\n"
    "  - id: helper\n    class: CommandLineTool\n    inputs:\n      helper_in: string\n    outputs: {}\n"
    "  - id: main\n    class: Workflow\n    inputs:\n      main_in: string\n    outputs: {}\n    steps: {}\n"
)


class TestParser:
    def test_required_vs_default_vs_optional(self):
        parsed = cwl_inputs.parse_cwl_inputs(
            {"inputs": {"a": "string", "b": {"type": "int", "default": 1}, "c": "string?"}}
        )
        assert parsed["a"]["required"] is True
        assert parsed["b"]["required"] is False
        assert parsed["c"]["optional"] is True

    def test_list_form(self):
        parsed = cwl_inputs.parse_cwl_inputs(
            {"inputs": [{"id": "x", "type": "string"}, {"id": "y", "type": "int", "default": 2}]}
        )
        assert parsed["x"]["required"] is True
        assert parsed["y"]["required"] is False

    def test_null_union_is_optional(self):
        parsed = cwl_inputs.parse_cwl_inputs({"inputs": {"n": {"type": ["null", "string"]}}})
        assert parsed["n"]["optional"] is True

    def test_missing_inputs_block(self):
        assert cwl_inputs.parse_cwl_inputs({"class": "CommandLineTool"}) == {}

    def test_load_graph_resolves_main(self):
        doc = cwl_inputs.load_cwl_doc(_GRAPH_CWL)
        parsed = cwl_inputs.parse_cwl_inputs(doc)
        assert "main_in" in parsed and "helper_in" not in parsed

    def test_build_schema_flags_autofilled(self):
        doc = cwl_inputs.load_cwl_doc(_INLINE_CWL)
        schema = cwl_inputs.build_input_schema(doc)
        assert schema["job_id"]["autofilled"] is True
        assert schema["aoi"]["autofilled"] is False

    def test_fetch_rejects_non_http_scheme(self):
        import pytest

        with pytest.raises(ValueError):
            cwl_inputs.fetch_cwl_text("file:///etc/passwd")


class TestCwlInputsEndpoint:
    client = TestClient(app_api)

    def test_inline_cwl_returns_schema(self):
        resp = self.client.post(ENDPOINT, json={"cwl": _INLINE_CWL})
        assert resp.status_code == 200
        inputs = resp.json()["inputs"]
        assert inputs["aoi"]["required"] is True
        assert inputs["aoi"]["autofilled"] is False
        assert inputs["job_id"]["autofilled"] is True
        assert inputs["count"]["required"] is False

    def test_url_cwl_fetches_and_returns_schema(self):
        with patch.object(cwl_inputs, "fetch_cwl_text", return_value=_INLINE_CWL) as m:
            resp = self.client.post(ENDPOINT, json={"url": "https://example.com/tool.cwl"})
        assert resp.status_code == 200
        m.assert_called_once_with("https://example.com/tool.cwl")
        assert "aoi" in resp.json()["inputs"]

    def test_graph_cwl_over_http_returns_main_inputs(self):
        resp = self.client.post(ENDPOINT, json={"cwl": _GRAPH_CWL})
        assert resp.status_code == 200
        assert "main_in" in resp.json()["inputs"]

    def test_neither_url_nor_cwl_is_400(self):
        resp = self.client.post(ENDPOINT, json={})
        assert resp.status_code == 400

    def test_both_url_and_cwl_is_400(self):
        resp = self.client.post(ENDPOINT, json={"url": "https://x/y.cwl", "cwl": _INLINE_CWL})
        assert resp.status_code == 400

    def test_bad_scheme_url_is_400(self):
        resp = self.client.post(ENDPOINT, json={"url": "file:///etc/passwd"})
        assert resp.status_code == 400

    def test_unparseable_cwl_is_400(self):
        resp = self.client.post(ENDPOINT, json={"cwl": "::: not valid yaml ::: ["})
        assert resp.status_code == 400
