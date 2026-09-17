"""
TDD tests for compute_with_timeout (timeout.py).

Given: Dask computation completes within the timeout
When:  compute_with_timeout is called
Then:  result is returned normally

Given: Dask computation exceeds the timeout (cluster disconnects, hangs)
When:  compute_with_timeout is called
Then:  RuntimeError is raised with a user-facing message

Given: OPENEO_COMPUTE_TIMEOUT env var is set
When:  save_result triggers a computation
Then:  timeout respects the env var value
"""
import time
import pathlib
import importlib.util
import pytest


def _load_timeout_module():
    spec = importlib.util.spec_from_file_location(
        "timeout",
        pathlib.Path(__file__).parent.parent
        / "openeo_argoworkflows_executor"
        / "timeout.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- Given: fn completes within the timeout ---
# When: compute_with_timeout is called
# Then: return value is passed through
def test_compute_succeeds_when_fn_completes_in_time():
    mod = _load_timeout_module()
    result = mod.compute_with_timeout(lambda: 42, timeout=5)
    assert result == 42


# --- Given: fn blocks longer than the timeout ---
# When: compute_with_timeout is called
# Then: RuntimeError is raised (not a hang)
def test_compute_raises_runtime_error_on_timeout():
    mod = _load_timeout_module()

    def slow_fn():
        time.sleep(10)

    with pytest.raises(RuntimeError):
        mod.compute_with_timeout(slow_fn, timeout=0.1)


# --- Given: timeout fires ---
# When: RuntimeError is raised
# Then: message is user-friendly (mentions Dask / cluster)
def test_compute_timeout_error_message_is_user_friendly():
    mod = _load_timeout_module()

    def slow_fn():
        time.sleep(10)

    with pytest.raises(RuntimeError, match="(?i)dask|cluster|timeout"):
        mod.compute_with_timeout(slow_fn, timeout=0.1)


# --- Given: fn raises an exception (not a timeout) ---
# When: compute_with_timeout is called
# Then: the original exception propagates unchanged
def test_compute_propagates_non_timeout_exceptions():
    mod = _load_timeout_module()

    def exploding_fn():
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        mod.compute_with_timeout(exploding_fn, timeout=5)
