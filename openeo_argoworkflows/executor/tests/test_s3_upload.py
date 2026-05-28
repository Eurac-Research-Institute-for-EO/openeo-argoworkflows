"""
TDD tests for S3 upload helper (s3.py).

Given: save_result writes a local .nc file
When: S3 env vars are set
Then: the file is uploaded to S3 and the S3 URI is returned

Given: S3 env vars are NOT set
When: upload_to_s3 runs
Then: local file path is returned unchanged (backward compatible)
"""
import importlib.util
import os
import pathlib
import pytest
from unittest.mock import MagicMock, patch


# Load s3.py directly — avoids the package __init__.py which eagerly
# imports io.py (requires odc/xarray not installed in this test env)
def _load_s3_module():
    spec = importlib.util.spec_from_file_location(
        "s3",
        pathlib.Path(__file__).parent.parent /
        "openeo_argoworkflows_executor/extra_processes/process_implementations/s3.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_S3_MODULE_PATH = str(
    pathlib.Path(__file__).parent.parent /
    "openeo_argoworkflows_executor/extra_processes/process_implementations/s3.py"
)


def _make_env(monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.scientificnet.org")
    monkeypatch.setenv("S3_BUCKET", "eo-public")
    monkeypatch.setenv("S3_ACCESS_KEY", "testkey")
    monkeypatch.setenv("S3_SECRET_KEY", "testsecret")
    monkeypatch.setenv("OPENEO_USER_ID", "user-123")
    monkeypatch.setenv("OPENEO_JOB_ID", "job-456")


# --- Given: S3 env vars set ---
# When: upload_to_s3 is called with a local file path
# Then: file is uploaded and S3 URI returned
def test_upload_to_s3_returns_s3_uri(tmp_path, monkeypatch):
    _make_env(monkeypatch)
    local_file = tmp_path / "result.nc"
    local_file.write_bytes(b"fake netcdf content")

    mod = _load_s3_module()
    with patch.object(mod, "boto3") as mock_boto3:
        mock_boto3.client.return_value = MagicMock()
        result = mod.upload_to_s3(str(local_file))

    assert result.startswith("s3://eo-public/")
    assert "user-123" in result
    assert "job-456" in result
    assert result.endswith("result.nc")


# --- Given: S3 env vars set ---
# When: upload_to_s3 is called
# Then: boto3 client and upload_file called with correct args
def test_upload_to_s3_calls_boto3_correctly(tmp_path, monkeypatch):
    _make_env(monkeypatch)
    local_file = tmp_path / "result.nc"
    local_file.write_bytes(b"fake netcdf content")

    mod = _load_s3_module()
    with patch.object(mod, "boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mod.upload_to_s3(str(local_file))

    mock_boto3.client.assert_called_once_with(
        "s3",
        endpoint_url="https://s3.scientificnet.org",
        aws_access_key_id="testkey",
        aws_secret_access_key="testsecret",
    )
    mock_client.upload_file.assert_called_once()
    assert mock_client.upload_file.call_args[0][0] == str(local_file)
    assert mock_client.upload_file.call_args[0][1] == "eo-public"


# --- Given: S3 env vars NOT set ---
# When: upload_to_s3 is called
# Then: returns local path unchanged, no S3 call
def test_upload_to_s3_falls_back_to_local_when_no_env(tmp_path):
    for var in ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        os.environ.pop(var, None)

    local_file = tmp_path / "result.nc"
    local_file.write_bytes(b"fake netcdf content")

    mod = _load_s3_module()
    with patch.object(mod, "boto3") as mock_boto3:
        result = mod.upload_to_s3(str(local_file))
        mock_boto3.client.assert_not_called()

    assert result == str(local_file)


# --- Given: S3 upload raises exception ---
# When: boto3 upload_file fails
# Then: local path returned as fallback — job must not crash
def test_upload_to_s3_falls_back_on_error(tmp_path, monkeypatch):
    _make_env(monkeypatch)
    local_file = tmp_path / "result.nc"
    local_file.write_bytes(b"fake netcdf content")

    mod = _load_s3_module()
    with patch.object(mod, "boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_client.upload_file.side_effect = Exception("S3 unavailable")
        mock_boto3.client.return_value = mock_client
        result = mod.upload_to_s3(str(local_file))

    assert result == str(local_file)


# --- Given: OPENEO_USER_ID and OPENEO_JOB_ID env vars ---
# When: upload_to_s3 is called
# Then: S3 key follows pattern user_id/job_id/filename
def test_upload_to_s3_key_structure(tmp_path, monkeypatch):
    _make_env(monkeypatch)
    local_file = tmp_path / "abc123.nc"
    local_file.write_bytes(b"fake netcdf content")

    mod = _load_s3_module()
    with patch.object(mod, "boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mod.upload_to_s3(str(local_file))

    s3_key = mock_client.upload_file.call_args[0][2]
    assert s3_key == "user-123/job-456/abc123.nc"
