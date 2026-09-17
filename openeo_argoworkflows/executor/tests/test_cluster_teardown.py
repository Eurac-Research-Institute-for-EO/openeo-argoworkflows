"""Tests for gateway cluster teardown — issue #144.

TDD: tests written before implementation.

When a job fails mid-compute, the executor previously skipped the gateway
cluster shutdown (the exception propagated past it), leaking scheduler and
worker pods until CLUSTER_IDLE_TIMEOUT (1h). Teardown must run in a finally
and must never raise (so it can't mask the job's real error).
"""

from unittest.mock import MagicMock

from openeo_argoworkflows_executor.cli import _teardown_cluster


class TestTeardownCluster:

    def test_none_cluster_is_noop(self):
        # No gateway cluster (LOCAL or CWL job) — nothing to do, nothing raised
        _teardown_cluster(None, None)

    def test_open_cluster_is_shut_down(self):
        cluster = MagicMock()
        cluster.status = "running"
        gateway = MagicMock()

        _teardown_cluster(cluster, gateway)

        cluster.shutdown.assert_called_once()
        gateway.list_clusters.assert_not_called()

    def test_closed_cluster_reconnects_then_shuts_down(self):
        # Sub-workflow processing may have replaced the cluster; reconnect to
        # whatever the gateway still has and shut that down.
        cluster = MagicMock()
        cluster.status = "closed"
        replacement = MagicMock()
        gateway = MagicMock()
        gateway.list_clusters.return_value = [MagicMock(name="c1")]
        gateway.connect.return_value = replacement

        _teardown_cluster(cluster, gateway)

        replacement.shutdown.assert_called_once()

    def test_closed_cluster_no_survivors_still_shuts_down_original(self):
        cluster = MagicMock()
        cluster.status = "closed"
        gateway = MagicMock()
        gateway.list_clusters.return_value = []

        _teardown_cluster(cluster, gateway)

        # Calling shutdown on an already-closed cluster is safe (upstream comment)
        cluster.shutdown.assert_called_once()

    def test_teardown_never_raises(self):
        # If shutdown itself explodes (e.g. dead event loop after a compute
        # timeout), teardown must swallow it — the job's real error matters more.
        cluster = MagicMock()
        cluster.status = "running"
        cluster.shutdown.side_effect = RuntimeError(
            "cannot schedule new futures after shutdown"
        )

        _teardown_cluster(cluster, MagicMock())

    def test_teardown_never_raises_on_gateway_errors(self):
        cluster = MagicMock()
        cluster.status = "closed"
        gateway = MagicMock()
        gateway.list_clusters.side_effect = ConnectionError("gateway gone")

        _teardown_cluster(cluster, gateway)
