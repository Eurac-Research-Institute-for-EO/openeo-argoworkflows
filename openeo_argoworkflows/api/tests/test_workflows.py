"""Tests for executor_workflow — issue #105.

TDD: tests written before implementation.

The executor pod must receive CDSE S3 credentials as env vars so that
Calrissian can forward them to CWL tool pods via --pod-env-vars.
"""

from unittest.mock import patch, MagicMock, create_autospec
from hera.workflows import WorkflowsService
from openeo_argoworkflows_api.workflows import executor_workflow


def _make_workflow(monkeypatch, extra_env=None):
    """Build an executor workflow with mocked settings."""
    mock_settings = MagicMock()
    mock_settings.STAC_API_URL = "https://stac.eurac.edu"
    mock_settings.OPENEO_EXECUTOR_IMAGE = "ghcr.io/test/executor:latest"
    mock_settings.OPENEO_WORKSPACE_ROOT = "/user_workspaces"
    if extra_env:
        for k, v in extra_env.items():
            setattr(mock_settings, k, v)

    mock_service = create_autospec(WorkflowsService, instance=True)
    mock_service.namespace = "openeo"

    with patch("openeo_argoworkflows_api.workflows.ExtendedAppSettings", return_value=mock_settings):
        w = executor_workflow(
            service=mock_service,
            process_graph={"load1": {"process_id": "load_collection", "arguments": {}}},
            dask_profile={},
            user_profile={"OPENEO_JOB_ID": "job-123", "OPENEO_USER_ID": "user-456"},
        )
    return w


def _get_container_env(workflow) -> dict:
    """Extract env vars from the executor container as a name→EnvVar dict."""
    # templates[0] is the Steps entrypoint; templates[1] is the Container template
    template = workflow.templates[1]
    return {e.name: e for e in (template.container.env or [])}


class TestExecutorWorkflowEnv:

    def test_stac_api_url_always_present(self, monkeypatch):
        w = _make_workflow(monkeypatch)
        env = _get_container_env(w)
        assert "STAC_API_URL" in env
        assert env["STAC_API_URL"].value == "https://stac.eurac.edu"

    def test_cdse_credentials_injected_from_secret(self, monkeypatch):
        """AWS_* env vars must be sourced from the cdse-s3-credentials K8s secret."""
        w = _make_workflow(monkeypatch)
        env = _get_container_env(w)

        for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT_URL_S3"):
            assert var in env, f"{var} missing from executor pod env"
            e = env[var]
            assert e.value_from is not None, f"{var} has no valueFrom"
            secret_ref = e.value_from.secret_key_ref
            assert secret_ref.name == "cdse-s3-credentials", \
                f"{var} must come from secret 'cdse-s3-credentials', got '{secret_ref.name}'"
            assert secret_ref.key == var, \
                f"{var} secret key must match var name, got '{secret_ref.key}'"

    def test_aws_endpoint_url_key_is_correct(self, monkeypatch):
        """Verify AWS_ENDPOINT_URL_S3 specifically — easy to misspell."""
        w = _make_workflow(monkeypatch)
        env = _get_container_env(w)
        assert "AWS_ENDPOINT_URL_S3" in env
        assert env["AWS_ENDPOINT_URL_S3"].value_from.secret_key_ref.key == "AWS_ENDPOINT_URL_S3"


class TestExecutorWorkflowGdalEnv:
    """GDAL HTTP resilience env — issue #144.

    Stalled /vsicurl reads previously hung the executor until
    OPENEO_COMPUTE_TIMEOUT. The executor pod must abort low-speed transfers
    and retry instead, matching the dask-gateway worker pods (charts PR #10).
    """

    EXPECTED = {
        "GDAL_HTTP_CONNECTTIMEOUT": "10",
        "GDAL_HTTP_TIMEOUT": "120",
        "GDAL_HTTP_LOW_SPEED_TIME": "30",
        "GDAL_HTTP_LOW_SPEED_LIMIT": "1024",
        "GDAL_HTTP_MAX_RETRY": "5",
        "GDAL_HTTP_RETRY_DELAY": "2",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "VSI_CACHE": "TRUE",
    }

    def test_gdal_resilience_env_present(self, monkeypatch):
        w = _make_workflow(monkeypatch)
        env = _get_container_env(w)
        for name, value in self.EXPECTED.items():
            assert name in env, f"{name} missing from executor pod env"
            assert env[name].value == value, \
                f"{name} expected {value!r}, got {env[name].value!r}"


class TestExecutorWorkflowDeadline:

    def _make_deadline_workflow(self, deadline=7200, compute_timeout=600):
        mock_settings = MagicMock()
        mock_settings.STAC_API_URL = "https://stac.eurac.edu"
        mock_settings.OPENEO_EXECUTOR_IMAGE = "ghcr.io/test/executor:latest"
        mock_settings.OPENEO_WORKSPACE_ROOT = "/user_workspaces"
        mock_settings.OPENEO_EXECUTOR_DEADLINE = deadline
        mock_settings.OPENEO_COMPUTE_TIMEOUT = compute_timeout

        mock_service = create_autospec(WorkflowsService, instance=True)
        mock_service.namespace = "openeo"

        with patch("openeo_argoworkflows_api.workflows.ExtendedAppSettings", return_value=mock_settings):
            return executor_workflow(
                service=mock_service,
                process_graph={},
                dask_profile={},
                user_profile={"OPENEO_JOB_ID": "job-123", "OPENEO_USER_ID": "user-456"},
            )

    def test_active_deadline_seconds_is_set(self, monkeypatch):
        """Workflow must have active_deadline_seconds so Argo kills hung executor pods."""
        w = self._make_deadline_workflow(deadline=7200)
        assert w.active_deadline_seconds == 7200, \
            "Workflow.active_deadline_seconds must be set so Argo kills hung executor pods"

    def test_compute_timeout_injected_into_executor_pod(self, monkeypatch):
        """OPENEO_COMPUTE_TIMEOUT must be injected into the executor pod env."""
        w = self._make_deadline_workflow(compute_timeout=900)
        env = _get_container_env(w)
        assert "OPENEO_COMPUTE_TIMEOUT" in env, \
            "OPENEO_COMPUTE_TIMEOUT must be passed to executor pod — io.py reads it from env"
        assert env["OPENEO_COMPUTE_TIMEOUT"].value == "900"
