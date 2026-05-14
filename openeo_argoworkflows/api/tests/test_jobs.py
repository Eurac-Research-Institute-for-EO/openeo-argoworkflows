import datetime
import pytest
import uuid

from unittest.mock import MagicMock, patch

from openeo_fastapi.api.models import JobsRequest
from openeo_fastapi.api.types import Status
from openeo_fastapi.client.psql.engine import create, get

from openeo_argoworkflows_api.jobs import ArgoJobsRegister, ArgoJob


def _make_register(mock_settings, mock_links):
    return ArgoJobsRegister(links=mock_links, settings=mock_settings)


def _make_queued_job(user_id):
    job = ArgoJob(
        job_id=uuid.uuid4(),
        process_graph_id="testgraph",
        status="queued",
        user_id=user_id,
        created=datetime.datetime.now(),
        description="test job",
        process={"x": {"process_id": "load_collection", "arguments": {}}},
        workflowname=None,
    )
    create(job)
    return job


def _make_running_job(user_id):
    job = ArgoJob(
        job_id=uuid.uuid4(),
        process_graph_id="testgraph",
        status="running",
        user_id=user_id,
        created=datetime.datetime.now(),
        description="test job",
        process={"x": {"process_id": "load_collection", "arguments": {}}},
        workflowname="wf-abc-123",
    )
    create(job)
    return job


@pytest.mark.skip("Not ready")
def test_start_job(a_mock_user, a_mock_job, mock_links, mock_settings, mocked_validate_user):
    create(a_mock_job)
    argo_register = ArgoJobsRegister(links=mock_links, settings=mock_settings)
    resp = argo_register.start_job(a_mock_job.job_id, a_mock_user)
    assert resp.status_code == 202
    job = get(a_mock_job, a_mock_job.job_id)
    assert job.status.value == "queued"


@pytest.mark.skip("Not ready")
def test_run_sync_job(a_mock_user, a_mock_job, mock_links, mock_settings):
    create(a_mock_job)
    argo_register = ArgoJobsRegister(links=mock_links, settings=mock_settings)
    import json
    with open(mock_settings.OPENEO_WORKSPACE_ROOT.parent / "fake-simple-pg.json") as f:
        json_data = json.load(f)
    job = JobsRequest(title="test", process=json_data)
    resp = argo_register.process_sync_job(job, a_mock_user)
    assert resp.status_code == 502


def test_stop_queued_job_sets_canceled(mock_engine, a_mock_user, mock_links, mock_settings):
    """Stopping a queued job (no workflow yet) must set status to canceled without calling Argo."""
    job = _make_queued_job(a_mock_user.user_id)
    register = _make_register(mock_settings, mock_links)

    with patch.object(register, "workflows_service") as mock_ws:
        resp = register.stop_job(job.job_id, a_mock_user)

    mock_ws.stop_workflow.assert_not_called()
    assert resp.status_code == 204

    updated = get(get_model=ArgoJob, primary_key=job.job_id)
    assert updated.status == Status.canceled


def test_stop_running_job_calls_argo(mock_engine, a_mock_user, mock_links, mock_settings):
    """Stopping a running job must call Argo stop_workflow."""
    job = _make_running_job(a_mock_user.user_id)
    register = _make_register(mock_settings, mock_links)

    with patch.object(register, "workflows_service") as mock_ws:
        resp = register.stop_job(job.job_id, a_mock_user)

    mock_ws.stop_workflow.assert_called_once()
    assert resp.status_code == 204


def test_stop_finished_job_returns_400(mock_engine, a_mock_user, mock_links, mock_settings):
    """Stopping a finished job must return 400."""
    from fastapi import HTTPException
    job = ArgoJob(
        job_id=uuid.uuid4(),
        process_graph_id="testgraph",
        status="finished",
        user_id=a_mock_user.user_id,
        created=datetime.datetime.now(),
        description="test",
        process={"x": {}},
        workflowname="wf-done",
    )
    create(job)
    register = _make_register(mock_settings, mock_links)

    with pytest.raises(HTTPException) as exc:
        register.stop_job(job.job_id, a_mock_user)

    assert exc.value.status_code == 400
