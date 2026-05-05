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
