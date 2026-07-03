import datetime
import fakeredis
import pytest
import uuid

from unittest.mock import patch, MagicMock

from openeo_argoworkflows_api.tasks import _resolve_udps


def _make_udp_spec(pg: dict, params: list | None = None) -> dict:
    return {
        "id": "test_udp",
        "process_graph": pg,
        "parameters": params or [],
    }


@patch("openeo_argoworkflows_api.tasks.Redis")
def test_submit_job(mock_redis):
    assert True


def test_resolve_udps_passthrough_no_udp():
    """A plain process graph with no UDPs comes back unchanged."""
    pg = {
        "lc": {
            "process_id": "load_collection",
            "arguments": {"id": "S2", "spatial_extent": None, "temporal_extent": None},
            "result": True,
        }
    }
    user_id = uuid.uuid4()

    with patch("openeo_argoworkflows_api.tasks.get") as mock_get:
        resolved = _resolve_udps(pg, user_id)

    assert "lc" in resolved
    assert resolved["lc"]["process_id"] == "load_collection"
    mock_get.assert_not_called()


def test_resolve_udps_inlines_udp():
    """A UDP call is inlined — calling node expanded to its component nodes."""
    user_id = uuid.uuid4()

    udp_pg = {
        "add_step": {
            "process_id": "add",
            "arguments": {"x": {"from_parameter": "x"}, "y": 1},
            "result": True,
        }
    }

    pg = {
        "call_udp": {
            "process_id": "add_one",
            "namespace": str(user_id),
            "arguments": {"x": 5},
            "result": True,
        }
    }

    mock_udp = MagicMock()
    mock_udp.dict.return_value = _make_udp_spec(
        udp_pg,
        params=[{"name": "x", "schema": {"type": "number"}}],
    )

    with patch("openeo_argoworkflows_api.tasks.get", return_value=mock_udp):
        resolved = _resolve_udps(pg, user_id)

    assert "call_udp_add_step" in resolved
    assert resolved["call_udp_add_step"]["process_id"] == "add"
    assert resolved["call_udp_add_step"]["arguments"] == {"x": 5, "y": 1}


def test_resolve_udps_unknown_process_raises():
    """An unknown process_id that is not a UDP and not predefined raises."""
    user_id = uuid.uuid4()
    pg = {
        "call_missing": {
            "process_id": "nonexistent_process_xyz",
            "namespace": str(user_id),
            "arguments": {},
            "result": True,
        }
    }

    with patch("openeo_argoworkflows_api.tasks.get", return_value=None):
        with pytest.raises(Exception):
            _resolve_udps(pg, user_id)


def test_resolve_udps_does_not_call_db_for_predefined():
    """Predefined processes must not trigger a DB lookup."""
    pg = {
        "ndvi_step": {
            "process_id": "normalized_difference",
            "arguments": {"x": 0.5, "y": 0.1},
            "result": True,
        }
    }
    user_id = uuid.uuid4()

    with patch("openeo_argoworkflows_api.tasks.get") as mock_get:
        _resolve_udps(pg, user_id)

    mock_get.assert_not_called()


def test_resolve_udps_knows_run_cwl():
    """run_cwl is a predefined EURAC process (specs/run_cwl.json) — the
    resolver must NOT treat it as a UDP (#153: it crashed the queue-worker)."""
    pg = {
        "run1": {
            "process_id": "run_cwl",
            "arguments": {"cwl": "https://example/tool.cwl", "inputs": {}},
            "result": True,
        }
    }
    with patch("openeo_argoworkflows_api.tasks.get") as mock_get:
        resolved = _resolve_udps(pg, uuid.uuid4())

    assert resolved["run1"]["process_id"] == "run_cwl"
    mock_get.assert_not_called()


def test_submit_job_failure_marks_job_error():
    """If pre-submission work raises (e.g. UDP resolution), the job must be
    flipped to error — not left in 'queued' forever (#153)."""
    from openeo_argoworkflows_api.tasks import submit_job
    from openeo_fastapi.api.types import Status

    job = MagicMock()
    job.job_id = uuid.uuid4()
    job.user_id = uuid.uuid4()

    with patch("openeo_argoworkflows_api.tasks.WorkflowsService"), \
         patch("openeo_argoworkflows_api.tasks._resolve_udps", side_effect=ValueError("boom")), \
         patch("openeo_argoworkflows_api.tasks.modify") as mock_modify:
        submit_job(job)

    assert job.status == Status.error
    mock_modify.assert_called_once_with(job)
