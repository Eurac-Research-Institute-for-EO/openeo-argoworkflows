"""
S3 helpers for the API — pre-signed URL generation for job results.

When a job result was written to S3 (href starts with s3://), the API
generates a time-limited pre-signed HTTPS URL so the client can download
directly from the object store without proxying through the API pod.

If S3 is not configured or the href is not an S3 URI, the original href
is returned unchanged — backward compatible with PVC-backed results.

Required env vars (all must be set):
    S3_ENDPOINT_URL  — e.g. https://s3.scientificnet.org
    S3_BUCKET        — e.g. eo-public
    S3_ACCESS_KEY
    S3_SECRET_KEY
"""
import logging
import os

import boto3

logger = logging.getLogger(__name__)

_DEFAULT_EXPIRY = 7 * 24 * 3600  # 7 days, matches existing signed URL window


def is_s3_uri(href: str) -> bool:
    return href.startswith("s3://")


def generate_presigned_url(href: str, expiry_seconds: int = _DEFAULT_EXPIRY) -> str:
    """Return a pre-signed HTTPS URL for an S3 URI, or href unchanged if not S3."""
    if not is_s3_uri(href):
        return href

    endpoint_url = os.environ.get("S3_ENDPOINT_URL")
    bucket = os.environ.get("S3_BUCKET")
    access_key = os.environ.get("S3_ACCESS_KEY")
    secret_key = os.environ.get("S3_SECRET_KEY")

    if not all([endpoint_url, bucket, access_key, secret_key]):
        logger.warning("S3 not configured — cannot generate pre-signed URL for %s", href)
        return href

    # s3://bucket/key → extract key
    without_scheme = href[len("s3://"):]
    _bucket, _, key = without_scheme.partition("/")

    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry_seconds,
        )
        logger.info("Generated pre-signed URL for %s", href)
        return url
    except Exception as e:
        logger.error("Failed to generate pre-signed URL for %s: %s", href, e)
        return href
