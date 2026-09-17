"""Tests for process registration resilience — ported from dev PR #164.

TDD: tests written before the implementation change.

`_register_processes_from_module` eagerly built a {name: spec} dict for every
public function it found in an implementations module. Any helper that leaked
into that module's namespace without a matching JSON spec raised
AttributeError and took the whole executor down at startup — a single stray
export becomes a total outage.

That is exactly what happened in production: helpers from
`openeo_processes_dask.process_implementations.udf.dimension_helper` leaked via
a star-import and the executor died with

    AttributeError: module 'openeo_processes_dask.specs' has no attribute
    'assign_dimension_labels'

The root cause was fixed upstream in the fork (`__all__ = []`, tag
v2025.5.1-eurac.1.4), but the registry should degrade gracefully rather than
crash: skip the spec-less function, log a warning, register everything else.
"""

import logging
import sys
import types

import pytest

from openeo_argoworkflows_executor.executor import _register_processes_from_module


PKG = "fake_processes_pkg"


def _make_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture
def fake_package():
    """A package whose specs module is missing a spec for one implementation."""

    def good_proc(x):
        return x

    def orphan_proc(x):
        """A helper that leaked into the namespace with no matching spec."""
        return x

    impls = _make_module(
        f"{PKG}.process_implementations", good_proc=good_proc, orphan_proc=orphan_proc
    )
    # specs deliberately omits `orphan_proc`
    specs = _make_module(f"{PKG}.specs", good_proc={"id": "good_proc"})

    sys.modules[PKG] = _make_module(PKG)
    sys.modules[f"{PKG}.process_implementations"] = impls
    sys.modules[f"{PKG}.specs"] = specs
    try:
        yield
    finally:
        for name in (f"{PKG}.specs", f"{PKG}.process_implementations", PKG):
            sys.modules.pop(name, None)


class TestMissingSpecIsSkipped:

    def test_does_not_raise(self, fake_package):
        # Before the fix this raised AttributeError and killed executor startup.
        _register_processes_from_module({}, PKG)

    def test_registers_the_process_that_has_a_spec(self, fake_package):
        registry = _register_processes_from_module({}, PKG)
        assert "good_proc" in registry

    def test_omits_the_process_without_a_spec(self, fake_package):
        registry = _register_processes_from_module({}, PKG)
        assert "orphan_proc" not in registry

    def test_warns_about_the_skipped_process(self, fake_package, caplog):
        with caplog.at_level(logging.WARNING):
            _register_processes_from_module({}, PKG)
        assert "orphan_proc" in caplog.text

    def test_returns_the_registry(self, fake_package):
        registry = {}
        assert _register_processes_from_module(registry, PKG) is registry


class TestFullySpeccedPackageIsUnaffected:
    """The happy path must not regress: every spec'd process still registers."""

    def test_all_processes_registered(self):
        def a(x):
            return x

        def b(x):
            return x

        impls = _make_module(f"{PKG}.process_implementations", a=a, b=b)
        specs = _make_module(f"{PKG}.specs", a={"id": "a"}, b={"id": "b"})
        sys.modules[PKG] = _make_module(PKG)
        sys.modules[f"{PKG}.process_implementations"] = impls
        sys.modules[f"{PKG}.specs"] = specs
        try:
            registry = _register_processes_from_module({}, PKG)
            assert set(registry) == {"a", "b"}
        finally:
            for name in (f"{PKG}.specs", f"{PKG}.process_implementations", PKG):
                sys.modules.pop(name, None)
