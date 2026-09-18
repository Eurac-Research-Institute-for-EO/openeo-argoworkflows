import datetime
import importlib.metadata
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import fsspec
import pytest
from fakeredis import FakeStrictRedis
from openeo_fastapi.api.types import Link
from openeo_fastapi.client.auth import User

fs = fsspec.filesystem(protocol="file")

__version__ = importlib.metadata.version("openeo_fastapi")

ALEMBIC_DIR = Path(__file__).parent.parent / "openeo_argoworkflows_api/psql/"
OPENEO_WORKSPACE_ROOT = str(Path(__file__).parent / "data" / "out")

SETTINGS_DICT = {
    "API_DNS": "test.api.org",
    "API_TLS": "False",
    "API_TITLE": "OpenEO Argo Api",
    "API_DESCRIPTION": "Testing the OpenEO Argo Api",
    "STAC_API_URL": "http://test-stac-api.mock.com/api/",
    "OIDC_URL": "http://test-oidc-api.mock.com/api/",
    "OIDC_ORGANISATION": "issuer",
    "OPENEO_WORKSPACE_ROOT": OPENEO_WORKSPACE_ROOT,
    "ARGO_WORKFLOWS_SERVER": "http://not.real.argo.com/api/",
    "ARGO_WORKFLOWS_NAMESPACE": "testing",
    "ARGO_WORKFLOWS_TOKEN": "atoken",
    "OPENEO_EXECUTOR_IMAGE": "testimage:2024.6.1",
    "OPENEO_SIGN_KEY": "xx9Yp6whivS0wrC2CmIhxlJAMbfDugZw",
    "DASK_GATEWAY_SERVER": "http://not.real.dask-gateway.com/",
}

for k, v in SETTINGS_DICT.items():
    os.environ[k] = str(v)

from openeo_argoworkflows_api.jobs import ArgoJob
from openeo_argoworkflows_api.settings import ExtendedAppSettings


def mock_user():
    return User(user_id=uuid.uuid4(), oidc_sub="testuser@testing.eu")


def mock_job():
    return ArgoJob(
        job_id=uuid.uuid4(),
        process_graph_id="testgraph",
        status="created",
        user_id=uuid.uuid4(),
        created=datetime.datetime.now(),
        description="old description",
        process={"x": {"y": 2}},
    )


@pytest.fixture(scope="function")
def a_mock_user():
    return mock_user()


@pytest.fixture(scope="function")
def a_mock_job():
    return mock_job()


@pytest.fixture(scope="function")
def mock_links(uuid: uuid.UUID = uuid.uuid4()):
    return [
        Link(
            href="https://eodc.eu/",
            rel="about",
            type="text/html",
            title="Homepage of the service provider",
        )
    ]


@pytest.fixture(scope="session")
def postgresql_proc(request, tmp_path_factory):
    """Session-scoped PostgreSQL process to avoid OOM from per-test PG instances."""
    import os
    import shutil

    from pytest_postgresql.config import get_config
    from pytest_postgresql.executor import PostgreSQLExecutor
    from pytest_postgresql.janitor import DatabaseJanitor

    config = get_config(request)
    datadir = str(tmp_path_factory.mktemp("postgresql_data"))
    pg_ctl = config["exec"]
    if pg_ctl and not Path(pg_ctl).exists():
        pg_ctl = shutil.which("pg_ctl")
    if not pg_ctl:
        installed_pg_ctl = sorted(Path("/usr/lib/postgresql").glob("*/bin/pg_ctl"))
        if installed_pg_ctl:
            pg_ctl = str(installed_pg_ctl[-1])

    import port_for

    pg_port = port_for.get_port(config["port"])
    assert pg_port is not None

    executor = PostgreSQLExecutor(
        executable=pg_ctl,
        host=config["host"],
        port=pg_port,
        datadir=datadir,
        unixsocketdir=config["unixsocketdir"],
        logfile=os.path.join(datadir, "pg.log"),
        startparams=config["startparams"],
        dbname=config["dbname"],
        user=config["user"],
        password=config["password"],
        options=config["options"],
        postgres_options=config["postgres_options"],
    )
    with executor:
        executor.wait_for_postgres()
        with DatabaseJanitor(
            user=executor.user,
            host=executor.host,
            port=executor.port,
            template_dbname=executor.template_dbname,
            version=executor.version,
            password=executor.password,
        ) as janitor:
            for load_element in config["load"]:
                janitor.load(load_element)
            yield executor
    shutil.rmtree(datadir, ignore_errors=True)


@pytest.fixture(scope="function")
def mock_settings():
    return ExtendedAppSettings(**SETTINGS_DICT)


@pytest.fixture(autouse=True)
def mock_engine(postgresql):
    """Postgresql engine for SQLAlchemy."""
    import os
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from openeo_fastapi.client.psql import engine as engine_module

    os.chdir(Path(ALEMBIC_DIR))

    # Set the env vars that alembic will use for DB connection and run alembic engine from CLI!
    os.environ["POSTGRES_USER"] = postgresql.info.user
    os.environ["POSTGRES_PASSWORD"] = "postgres"
    os.environ["POSTGRESQL_HOST"] = postgresql.info.host
    os.environ["POSTGRESQL_PORT"] = str(postgresql.info.port)
    os.environ["POSTGRES_DB"] = postgresql.info.dbname
    os.environ["ALEMBIC_DIR"] = str(ALEMBIC_DIR)

    alembic_cfg = Config("alembic.ini")

    command.upgrade(alembic_cfg, "head")

    if engine_module._engine is not None:
        engine_module._engine.dispose()
    engine_module._engine = None

    engine = engine_module.get_engine()
    yield engine
    engine.dispose()
    engine_module._engine = None


@pytest.fixture(scope="module", autouse=True)
def cleanup_out_folder():
    if not fs.exists(OPENEO_WORKSPACE_ROOT):
        fs.mkdir(OPENEO_WORKSPACE_ROOT)

    yield  # Yield to the running tests

    # Teardown: Delete the output folder,
    if fs.exists(OPENEO_WORKSPACE_ROOT):
        fs.rm(OPENEO_WORKSPACE_ROOT, recursive=True)


@pytest.fixture
def redis_conn():
    return FakeStrictRedis(decode_responses=True)


def mock_auth(status_code=200):
    import json

    from fastapi import Response

    user = mock_user()

    if not status_code:
        status_code = 200
    fake_sub_id = "testuser@eodc.eu"
    test_resp_content_bytes = json.dumps(
        {
            "userinfo_endpoint": "http://a.fake_url_again.cloud/",
            "eduperson_entitlement": [
                "urn:mace:egi.eu:group:vo.openeo.cloud:role=early_adopter#aai.egi.eu",
                "urn:mace:egi.eu:group:vo.openeo.cloud:role=platform_developer#aai.egi.eu",
            ],
            "sub": user.oidc_sub,
        }
    ).encode("utf-8")

    mocked_response = Response()
    mocked_response.status_code = status_code
    mocked_response._content = test_resp_content_bytes

    return {"user": user.user_id, "sub": user.oidc_sub, "resp": mocked_response}
