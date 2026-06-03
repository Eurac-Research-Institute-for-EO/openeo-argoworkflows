"""
compute_with_timeout — run a callable in a thread with a hard deadline.

Used to wrap Dask materialisation calls (to_netcdf, etc.) so that if the
Dask cluster disconnects mid-computation the executor pod raises a clean
exception instead of hanging indefinitely.
"""
import concurrent.futures
import logging

logger = logging.getLogger(__name__)


def compute_with_timeout(fn, *args, timeout: float = 600, **kwargs):
    """Call fn(*args, **kwargs) and raise RuntimeError if it exceeds timeout seconds.

    Any exception raised by fn (other than TimeoutError) propagates unchanged.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error(
                "Dask computation timed out after %ss — cluster may have disconnected",
                timeout,
            )
            raise RuntimeError(
                f"Dask computation timed out after {timeout:.0f}s. "
                "The Dask cluster may have disconnected during processing. "
                "Try reducing your spatial or temporal extent, or retry the job."
            )
