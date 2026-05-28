"""
TDD tests for S3 pre-signed URL generation in get_results.

Given: a finished job whose result file lives on S3
When: GET /jobs/{id}/results is called
Then: asset hrefs are S3 pre-signed URLs, not local file paths

Given: a finished job whose result file lives on local PVC (no S3)
When: GET /jobs/{id}/results is called
Then: asset hrefs are the existing signed API file URLs (backward compatible)
"""
import datetime
import uuid
import json
import pytest

from unittest.mock import MagicMock, patch
from openeo_fastapi.client.psql.engine import create

from openeo_argoworkflows_api.jobs import ArgoJob
from openeo_argoworkflows_api.s3 import generate_presigned_url, is_s3_uri


# --- unit tests for s3 helpers ---

# Given: an S3 URI
# When: is_s3_uri is called
# Then: returns True
def test_is_s3_uri_true():
    assert is_s3_uri("s3://eo-public/user-123/job-456/result.nc") is True


# Given: a local path
# When: is_s3_uri is called
# Then: returns False
def test_is_s3_uri_false():
    assert is_s3_uri("/user_workspaces/user-123/job-456/RESULTS/result.nc") is False


# Given: S3 env vars set
# When: generate_presigned_url is called with an S3 URI
# Then: returns an https URL with expiry
def test_generate_presigned_url_returns_https(monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.scientificnet.org")
    monkeypatch.setenv("S3_BUCKET", "eo-public")
    monkeypatch.setenv("S3_ACCESS_KEY", "testkey")
    monkeypatch.setenv("S3_SECRET_KEY", "testsecret")

    from openeo_argoworkflows_api import s3 as s3_mod

    with patch.object(s3_mod, "boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = (
            "https://eo-public.s3.scientificnet.org/user-123/job-456/result.nc?X-Amz-Expires=604800"
        )
        mock_boto3.client.return_value = mock_client

        url = generate_presigned_url("s3://eo-public/user-123/job-456/result.nc", expiry_seconds=604800)

    assert url.startswith("https://")
    mock_client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "eo-public", "Key": "user-123/job-456/result.nc"},
        ExpiresIn=604800,
    )


# Given: generate_presigned_url called with a non-S3 URI
# When: is_s3_uri returns False
# Then: returns the original href unchanged
def test_generate_presigned_url_passthrough_for_non_s3():
    local_href = "https://openeo.eurac.edu/openeo/1.0/files/job-456/RESULTS/result.nc"
    result = generate_presigned_url(local_href)
    assert result == local_href
