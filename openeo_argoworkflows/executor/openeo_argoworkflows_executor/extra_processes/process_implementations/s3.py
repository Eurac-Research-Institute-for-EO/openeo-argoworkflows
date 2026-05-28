"""
S3 upload helper for save_result.

Uploads a local result file to the S3 object store after it has been
written to disk. Falls back to returning the local path if S3 env vars
are not configured or if the upload fails — the job must not crash.

Required env vars (all must be set for upload to activate):
    S3_ENDPOINT_URL  — e.g. https://s3.scientificnet.org
    S3_BUCKET        — e.g. eo-public
    S3_ACCESS_KEY
    S3_SECRET_KEY

Optional env vars (used to build the S3 key prefix):
    OPENEO_USER_ID   — user UUID
    OPENEO_JOB_ID    — job UUID
"""
import logging
import os
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)


def is_s3_configured() -> bool:
    return all(os.environ.get(v) for v in ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"))


def upload_to_s3(local_path: str) -> str:
    """Upload a local file to S3 and return its S3 URI.

    Returns the local path unchanged if S3 is not configured or upload fails.
    """
    endpoint_url = os.environ.get("S3_ENDPOINT_URL")
    bucket = os.environ.get("S3_BUCKET")
    access_key = os.environ.get("S3_ACCESS_KEY")
    secret_key = os.environ.get("S3_SECRET_KEY")

    if not all([endpoint_url, bucket, access_key, secret_key]):
        logger.info("S3 not configured — keeping local path: %s", local_path)
        return local_path

    user_id = os.environ.get("OPENEO_USER_ID", "unknown-user")
    job_id = os.environ.get("OPENEO_JOB_ID", "unknown-job")
    filename = Path(local_path).name
    s3_key = f"{user_id}/{job_id}/{filename}"

    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        client.upload_file(local_path, bucket, s3_key)
        s3_uri = f"s3://{bucket}/{s3_key}"
        logger.info("Uploaded result to S3: %s", s3_uri)
        return s3_uri
    except Exception as e:
        logger.error("S3 upload failed, keeping local path. Error: %s", e)
        return local_path
