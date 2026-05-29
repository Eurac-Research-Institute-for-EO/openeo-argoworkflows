"""
TDD tests for S3 public URL resolution in get_results.

Given: a finished job whose result file lives on S3
When: GET /jobs/{id}/results is called
Then: asset hrefs are direct public HTTPS URLs (eo-public is publicly readable)

Given: a finished job whose result file lives on local PVC (no S3)
When: GET /jobs/{id}/results is called
Then: asset hrefs are the existing signed API file URLs (backward compatible)
"""
import os
import pytest

from openeo_argoworkflows_api.s3 import generate_presigned_url, is_s3_uri


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


# Given: an S3 URI with job_id and api_base
# When: generate_presigned_url is called
# Then: returns a proxy URL through the API (no CORS issue)
def test_generate_presigned_url_returns_proxy_url():
    url = generate_presigned_url(
        "s3://eo-public/user-123/job-456/result.nc",
        job_id="job-456",
        api_base="https://openeo.eurac.edu",
        openeo_prefix="/openeo/1.1.0",
    )
    assert url == "https://openeo.eurac.edu/openeo/1.1.0/jobs/job-456/results/download/result.nc"


# Given: an S3 URI without job_id/api_base
# When: generate_presigned_url is called
# Then: falls back to direct public URL
def test_generate_presigned_url_fallback_to_direct(monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.scientificnet.org")
    url = generate_presigned_url("s3://eo-public/user-123/job-456/result.nc")
    assert url == "https://s3.scientificnet.org/eo-public/user-123/job-456/result.nc"


# Given: generate_presigned_url called with a non-S3 URI
# When: is_s3_uri returns False
# Then: returns the original href unchanged
def test_generate_presigned_url_passthrough_for_non_s3():
    local_href = "https://openeo.eurac.edu/openeo/1.0/files/job-456/RESULTS/result.nc"
    result = generate_presigned_url(local_href)
    assert result == local_href


# Given: S3_ENDPOINT_URL not set, no job_id/api_base
# When: generate_presigned_url is called
# Then: falls back to default scientificnet endpoint
def test_generate_presigned_url_uses_default_endpoint():
    os.environ.pop("S3_ENDPOINT_URL", None)
    url = generate_presigned_url("s3://eo-public/user-123/job-456/result.nc")
    assert "s3.scientificnet.org" in url
    assert "eo-public" in url
