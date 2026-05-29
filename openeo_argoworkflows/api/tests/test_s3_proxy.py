"""
TDD tests for S3 proxy download endpoint.

Given: a finished job with results on S3
When: GET /jobs/{id}/results/download is called
Then: file is streamed back to client with correct headers

This endpoint solves the CORS problem — browser talks to openeo.eurac.edu
(same origin as the web editor) and the API fetches from S3 internally.
"""
import uuid
import datetime
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from openeo_fastapi.client.psql.engine import create

from openeo_argoworkflows_api.jobs import ArgoJob
from openeo_argoworkflows_api.s3 import s3_download_stream


# --- unit test for s3_download_stream helper ---

# Given: a valid s3:// URI and configured credentials
# When: s3_download_stream is called
# Then: returns the streaming body and content-length
def test_s3_download_stream_calls_get_object(monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.scientificnet.org")
    monkeypatch.setenv("S3_BUCKET", "eo-public")
    monkeypatch.setenv("S3_ACCESS_KEY", "testkey")
    monkeypatch.setenv("S3_SECRET_KEY", "testsecret")

    from openeo_argoworkflows_api import s3 as s3_mod

    mock_body = MagicMock()
    mock_body.iter_chunks.return_value = iter([b"chunk1", b"chunk2"])

    with patch.object(s3_mod, "boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_client.get_object.return_value = {
            "Body": mock_body,
            "ContentLength": 12,
            "ContentType": "binary/octet-stream",
        }
        mock_boto3.client.return_value = mock_client

        body, length, content_type = s3_download_stream(
            "s3://eo-public/user-123/job-456/result.nc"
        )

    mock_client.get_object.assert_called_once_with(
        Bucket="eo-public", Key="user-123/job-456/result.nc"
    )
    assert length == 12
    assert content_type == "binary/octet-stream"


# Given: a non-S3 href
# When: s3_download_stream is called
# Then: raises ValueError
def test_s3_download_stream_rejects_non_s3_uri():
    from openeo_argoworkflows_api.s3 import s3_download_stream
    with pytest.raises(ValueError, match="s3://"):
        s3_download_stream("/local/path/result.nc")
