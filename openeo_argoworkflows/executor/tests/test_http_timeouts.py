"""Tests for post-processing HTTP timeouts — issue #147.

TDD: tests written before implementation.

A job wedged indefinitely after successful compute because the STAC results
publishing used bare requests.post (no timeout) and blocked on a half-dead
connection. All post-compute HTTP calls must carry a timeout, and the two
publishing call sites (cli.py regular path, stac_cwl.py CWL path) must go
through the shared helper.
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from openeo_argoworkflows_executor.http import post_json, HTTP_TIMEOUT


class TestPostJson:

    def test_passes_timeout_to_requests(self):
        with patch("openeo_argoworkflows_executor.http.requests") as req:
            post_json("https://stac.example/collections", {"id": "c1"})
        req.post.assert_called_once_with(
            "https://stac.example/collections", json={"id": "c1"}, timeout=HTTP_TIMEOUT
        )

    def test_returns_response(self):
        with patch("openeo_argoworkflows_executor.http.requests") as req:
            req.post.return_value = MagicMock(status_code=201)
            r = post_json("https://stac.example/x", {})
        assert r.status_code == 201

    def test_exceptions_propagate(self):
        # Callers wrap publishing in try/except — the helper must not swallow,
        # so a broken STAC API still triggers the callers' fallback paths.
        with patch("openeo_argoworkflows_executor.http.requests") as req:
            req.post.side_effect = ConnectionError("dead")
            with pytest.raises(ConnectionError):
                post_json("https://stac.example/x", {})

    def test_timeout_is_finite_tuple(self):
        # (connect, read) — both bounded so a stalled socket can't hang a job
        assert isinstance(HTTP_TIMEOUT, tuple) and len(HTTP_TIMEOUT) == 2
        assert all(isinstance(t, (int, float)) and t > 0 for t in HTTP_TIMEOUT)


class TestNoBarePostsRemain:
    """Static check: publishing modules must not call requests.post directly."""

    @pytest.mark.parametrize("module", ["cli.py", "stac_cwl.py"])
    def test_module_has_no_bare_requests_post(self, module):
        src = (
            Path(__file__).parent.parent
            / "openeo_argoworkflows_executor"
            / module
        ).read_text()
        tree = ast.parse(src)
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in ("post", "get", "put")
            and isinstance(node.value, ast.Name)
            and node.value.id == "requests"
        ]
        assert not offenders, (
            f"{module} lines {offenders}: bare requests.<verb> — "
            "use openeo_argoworkflows_executor.http.post_json (has timeout, #147)"
        )
