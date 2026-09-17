"""
TDD tests for reconcile.py

Given a set of jobs in DB with status 'running'
When reconcile() is called
Then job statuses are synced against Argo workflow state
"""
import datetime
import uuid
import pytest

from unittest.mock import MagicMock, patch
from openeo_fastapi.api.types import Status
from openeo_fastapi.client.psql.engine import create, get

from openeo_argoworkflows_api.jobs import ArgoJob
from openeo_argoworkflows_api.reconcile import reconcile


def _make_running_job(user_id):
    job = ArgoJob(
        job_id=uuid.uuid4(),
        process_graph_id="testgraph",
        status="running",
        user_id=user_id,
        created=datetime.datetime.now(),
        description="test",
        process={"x": {}},
        workflowname="wf-test-123",
    )
    create(job)
    return job


def _make_running_job_no_workflow(user_id):
    job = ArgoJob(
        job_id=uuid.uuid4(),
        process_graph_id="testgraph",
        status="running",
        user_id=user_id,
        created=datetime.datetime.now(),
        description="test",
        process={"x": {}},
        workflowname=None,
    )
    create(job)
    return job


def _mock_workflow(phase, oom=False):
    wf = MagicMock()
    wf.status.phase = phase
    if oom:
        node = MagicMock()
        node.message = "OOMKilled"
        wf.status.nodes = {"node1": node}
    else:
        wf.status.nodes = {}
    return wf


# --- Given: a running job whose Argo workflow succeeded ---
# When: reconcile() runs
# Then: job status → finished
def test_reconcile_succeeded_job(mock_engine, a_mock_user):
    job = _make_running_job(a_mock_user.user_id)

    with patch("openeo_argoworkflows_api.reconcile.WorkflowsService") as mock_ws_cls:
        mock_ws_cls.return_value.get_workflow.return_value = _mock_workflow("Succeeded")
        reconcile()

    updated = get(get_model=ArgoJob, primary_key=job.job_id)
    assert updated.status == Status.finished
    assert updated.message is None


# --- Given: a running job whose Argo workflow failed ---
# When: reconcile() runs
# Then: job status → error with generic message
def test_reconcile_failed_job(mock_engine, a_mock_user):
    job = _make_running_job(a_mock_user.user_id)

    with patch("openeo_argoworkflows_api.reconcile.WorkflowsService") as mock_ws_cls:
        mock_ws_cls.return_value.get_workflow.return_value = _mock_workflow("Failed")
        reconcile()

    updated = get(get_model=ArgoJob, primary_key=job.job_id)
    assert updated.status == Status.error
    assert updated.message is not None
    assert "failed" in updated.message.lower()


# --- Given: a running job whose Argo workflow OOMKilled ---
# When: reconcile() runs
# Then: job status → error with OOM-specific message
def test_reconcile_oom_job(mock_engine, a_mock_user):
    job = _make_running_job(a_mock_user.user_id)

    with patch("openeo_argoworkflows_api.reconcile.WorkflowsService") as mock_ws_cls:
        mock_ws_cls.return_value.get_workflow.return_value = _mock_workflow("Failed", oom=True)
        reconcile()

    updated = get(get_model=ArgoJob, primary_key=job.job_id)
    assert updated.status == Status.error
    assert "memory" in updated.message.lower()


# --- Given: a running job whose Argo workflow no longer exists ---
# When: reconcile() runs
# Then: job status → error with not-found message
def test_reconcile_workflow_not_found(mock_engine, a_mock_user):
    job = _make_running_job(a_mock_user.user_id)

    with patch("openeo_argoworkflows_api.reconcile.WorkflowsService") as mock_ws_cls:
        mock_ws_cls.return_value.get_workflow.side_effect = Exception("not found")
        reconcile()

    updated = get(get_model=ArgoJob, primary_key=job.job_id)
    assert updated.status == Status.error
    assert updated.message is not None


# --- Given: a running job with no workflowname ---
# When: reconcile() runs
# Then: job status → error immediately (no Argo call)
def test_reconcile_no_workflowname(mock_engine, a_mock_user):
    job = _make_running_job_no_workflow(a_mock_user.user_id)

    with patch("openeo_argoworkflows_api.reconcile.WorkflowsService") as mock_ws_cls:
        reconcile()
        mock_ws_cls.return_value.get_workflow.assert_not_called()

    updated = get(get_model=ArgoJob, primary_key=job.job_id)
    assert updated.status == Status.error
    assert updated.message is not None


# --- Given: a running job whose Argo workflow is still running ---
# When: reconcile() runs
# Then: job status unchanged — still running
def test_reconcile_still_running_job(mock_engine, a_mock_user):
    job = _make_running_job(a_mock_user.user_id)

    with patch("openeo_argoworkflows_api.reconcile.WorkflowsService") as mock_ws_cls:
        mock_ws_cls.return_value.get_workflow.return_value = _mock_workflow("Running")
        reconcile()

    updated = get(get_model=ArgoJob, primary_key=job.job_id)
    assert updated.status == Status.running
