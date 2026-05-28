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

logger = logging.getLogger(__name__)


def is_s3_uri(href: str) -> bool:
    return href.startswith("s3://")


def generate_presigned_url(href: str, expiry_seconds: int = 0) -> str:
    """Return a direct public HTTPS URL for an S3 URI, or href unchanged if not S3."""
    if not is_s3_uri(href):
        return href

    endpoint_url = os.environ.get("S3_ENDPOINT_URL", "https://s3.scientificnet.org")

    # s3://bucket/key → https://endpoint/bucket/key
    without_scheme = href[len("s3://"):]
    url = f"{endpoint_url.rstrip('/')}/{without_scheme}"
    logger.info("Resolved S3 URI to public URL: %s", url)
    return url
