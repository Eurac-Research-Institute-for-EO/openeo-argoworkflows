"""No module in the executor package may shadow a stdlib top-level module.

TDD: tests written before the rename.

The package is copied to /opt/openeo_argoworkflows_executor in the image, and
that path is also the container WORKDIR. A module named http.py therefore
shadows Python's stdlib `http` package for anything that runs with the package
directory on sys.path[0] -- which breaks urllib3, and with it every outbound
HTTP call:

    ModuleNotFoundError: No module named 'http.client'; 'http' is not a package

The normal entry point (CMD ["openeo_executor"]) puts .venv/bin on sys.path[0]
rather than the work directory, so stdlib http still wins there. That makes
this latent rather than active -- it bites any `python -c ...` or
`python script.py` run from the package directory, which is exactly what
debugging a container looks like.

These tests assert the general invariant, so a future module named json.py,
types.py or logging.py is caught here instead of in an image.
"""

import subprocess
import sys
from pathlib import Path

import pytest


PACKAGE_DIR = Path(__file__).parent.parent / "openeo_argoworkflows_executor"


def _package_module_names():
    return sorted(
        p.stem
        for p in PACKAGE_DIR.glob("*.py")
        if p.stem != "__init__"
    )


def test_package_dir_exists():
    """Guard the guard: a wrong path would make the tests below vacuous."""
    assert PACKAGE_DIR.is_dir(), PACKAGE_DIR
    assert _package_module_names()


@pytest.mark.parametrize("name", _package_module_names())
def test_module_does_not_shadow_stdlib(name):
    assert name not in sys.stdlib_module_names, (
        f"{name}.py shadows the stdlib module {name!r}. The package directory is "
        "the image WORKDIR, so this breaks any interpreter started from there. "
        "Rename it (e.g. http.py -> http_utils.py)."
    )


def test_stdlib_http_importable_from_package_dir():
    """The failure this guards, reproduced end to end."""
    result = subprocess.run(
        [sys.executable, "-c", "import http.client; print(http.__file__)"],
        cwd=PACKAGE_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "stdlib 'http' is shadowed when running from the package directory:\n"
        f"{result.stderr}"
    )
    assert "site-packages" not in result.stdout


def test_urllib3_importable_from_package_dir():
    """urllib3 is what actually breaks, via six's lazy http.client import."""
    result = subprocess.run(
        [sys.executable, "-c", "import urllib3; print('ok')"],
        cwd=PACKAGE_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"urllib3 fails to import from the package directory:\n{result.stderr}"
    )
