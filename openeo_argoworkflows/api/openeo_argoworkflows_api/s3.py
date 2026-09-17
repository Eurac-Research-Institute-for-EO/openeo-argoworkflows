"""
S3 helpers for the API — direct public URL construction for job results.

The eo-public bucket on CEPH has public read access, so we return a direct
HTTPS URL rather than a pre-signed URL (CEPH RadosGW pre-signed URLs return
403 even for public buckets due to signature compatibility issues).

If the href is not an S3 URI the original href is returned unchanged —
backward compatible with PVC-backed results.
"""
import logging
import os
from typing import Tuple

import boto3

logger = logging.getLogger(__name__)


def is_s3_uri(href: str) -> bool:
    return href.startswith("s3://")


def generate_presigned_url(href: str, job_id: str = None, api_base: str = None, openeo_prefix: str = "/openeo/1.1.0", expiry_seconds: int = 0) -> str:
    """Return a proxy download URL for an S3 URI, or href unchanged if not S3.

    Routes the download through the API pod to avoid CORS issues with CEPH.
    Falls back to direct S3 URL if job_id or api_base not provided.
    """
    if not is_s3_uri(href):
        return href

    # Build proxy URL if we have the context to do so
    if job_id and api_base:
        filename = href.split("/")[-1]
        url = f"{api_base.rstrip('/')}{openeo_prefix}/jobs/{job_id}/results/download/{filename}"
        logger.info("Resolved S3 URI to proxy URL: %s", url)
        return url

    # Fallback: direct public URL
    endpoint_url = os.environ.get("S3_ENDPOINT_URL", "https://s3.scientificnet.org")
    without_scheme = href[len("s3://"):]
    url = f"{endpoint_url.rstrip('/')}/{without_scheme}"
    logger.info("Resolved S3 URI to public URL: %s", url)
    return url


def s3_download_stream(href: str) -> Tuple:
    """Fetch an S3 object and return (body, content_length, content_type).

    Used by the proxy download endpoint to stream files to the browser,
    bypassing CORS restrictions on the CEPH bucket.

    Raises ValueError if href is not an s3:// URI.
    """
    if not is_s3_uri(href):
        raise ValueError(f"Expected an s3:// URI, got: {href}")

    endpoint_url = os.environ.get("S3_ENDPOINT_URL", "https://s3.scientificnet.org")
    bucket = os.environ.get("S3_BUCKET", "eo-public")
    access_key = os.environ.get("S3_ACCESS_KEY")
    secret_key = os.environ.get("S3_SECRET_KEY")

    without_scheme = href[len("s3://"):]
    _bucket, _, key = without_scheme.partition("/")

    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    resp = client.get_object(Bucket=bucket, Key=key)
    return (
        resp["Body"],
        resp.get("ContentLength"),
        resp.get("ContentType", "application/octet-stream"),
    )
