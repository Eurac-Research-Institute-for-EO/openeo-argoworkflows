"""Timeout-bounded HTTP helpers for post-processing calls (#147).

A job hung indefinitely after successful compute because STAC results
publishing used bare requests.post: no timeout means a half-dead connection
blocks forever (until the Argo workflow deadline). Every post-compute HTTP
call must be bounded; callers keep their own try/except fallbacks.
"""

import requests

# (connect, read) seconds. Generous read window for a slow STAC API, but
# finite so a stalled socket can never wedge the executor.
HTTP_TIMEOUT = (10, 60)


def post_json(url, payload):
    """requests.post with a bounded timeout. Exceptions propagate to callers."""
    return requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
