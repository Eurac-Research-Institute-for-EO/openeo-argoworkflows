"""Tests for deterministic executor exit — issue #147 (second root cause).

TDD: tests written before implementation.

Even after #148 (HTTP timeouts), a job wedged AFTER all work completed:
results written, cluster torn down, but the interpreter never exited — 68
threads parked in futex-wait, one event loop polling forever (dask /
dask-gateway client finalizers deadlocking atexit). The workflow only
succeeds when the process exits, so the job hung in "running".

Fix: _close_dask() best-effort closes the client + gateway session before
exit, and the CLI ends the success path with os._exit(0) to bypass
deadlock-prone finalizers.
"""

from unittest.mock import MagicMock

from openeo_argoworkflows_executor.cli import _close_dask


class TestCloseDask:
    def test_none_args_are_noop(self):
        _close_dask(None, None, None)

    def test_closes_client_and_gateway(self):
        client, gateway = MagicMock(), MagicMock()
        _close_dask(client, gateway, None)
        client.close.assert_called_once()
        gateway.close.assert_called_once()

    def test_closes_local_cluster(self):
        # LOCAL mode (stable): the in-process threaded LocalCluster must be
        # closed too — its worker threads hold HDF5 state from the compute and
        # deadlock the post-processing xr.open_dataset if left alive (#147,
        # observed on stable only; gateway mode computes in separate pods).
        client, local_cluster = MagicMock(), MagicMock()
        _close_dask(client, None, local_cluster)
        client.close.assert_called_once()
        local_cluster.close.assert_called_once()

    def test_client_close_error_does_not_skip_others(self):
        client, gateway, local_cluster = MagicMock(), MagicMock(), MagicMock()
        client.close.side_effect = RuntimeError("loop is closed")
        _close_dask(client, gateway, local_cluster)
        gateway.close.assert_called_once()
        local_cluster.close.assert_called_once()

    def test_never_raises(self):
        client, gateway, local_cluster = MagicMock(), MagicMock(), MagicMock()
        client.close.side_effect = RuntimeError("boom")
        gateway.close.side_effect = ConnectionError("gone")
        local_cluster.close.side_effect = TimeoutError("stuck")
        _close_dask(client, gateway, local_cluster)


class TestSuccessPathHardExit:
    """The CLI must end its success path with os._exit(0) (static check —
    invoking the real command needs the full image deps)."""

    def test_execute_ends_with_hard_exit(self):
        import inspect

        from openeo_argoworkflows_executor import cli

        src = inspect.getsource(cli)
        assert "os._exit(0)" in src, (
            "execute() must end the success path with os._exit(0) — normal "
            "interpreter shutdown deadlocks on dask/aiohttp finalizers (#147)"
        )
