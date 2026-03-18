FROM python:3.11-slim as prod

WORKDIR /opt

ARG USER_ID=1000
ARG GROUP_ID=1000
ARG POETRY_VERSION="1.8.3"

ENV USERNAME=api
ENV POETRY_VIRTUALENVS_IN_PROJECT 1
ENV POETRY_HOME=/opt/poetry
ENV VIRTUAL_ENV=/opt/openeo_argoworkflows_api/.venv

COPY ./README.md ./pyproject.toml ./poetry.lock /opt/

RUN python3 -m venv ENV_DIR $VIRTUAL_ENV && \
    $VIRTUAL_ENV/bin/pip install -U pip setuptools && \
    $VIRTUAL_ENV/bin/pip install poetry==${POETRY_VERSION}

ENV PATH="${POETRY_HOME}/bin:${VIRTUAL_ENV}/bin:${PATH}"

RUN poetry install --only main

# Patch openeo-fastapi library:
# - core.py: env-based OIDC config (replace hardcoded EGI Check-in)
# - auth.py: disable JWT audience validation for EURAC Keycloak
# - types.py: extend Dimension model to preserve band names and spatial metadata
# - collections.py: normalize dimension names to OpenEO standard (x, y, t, bands)
COPY ./patches/openeo_fastapi_core.py ${VIRTUAL_ENV}/lib/python3.11/site-packages/openeo_fastapi/client/core.py
COPY ./patches/openeo_fastapi_auth.py ${VIRTUAL_ENV}/lib/python3.11/site-packages/openeo_fastapi/client/auth.py
COPY ./patches/openeo_fastapi_types.py ${VIRTUAL_ENV}/lib/python3.11/site-packages/openeo_fastapi/api/types.py
COPY ./patches/openeo_fastapi_collections.py ${VIRTUAL_ENV}/lib/python3.11/site-packages/openeo_fastapi/client/collections.py

COPY --chown=${USER_ID}:${GROUP_ID} ./openeo_argoworkflows_api /opt/openeo_argoworkflows_api

RUN addgroup --gid ${GROUP_ID} ${USERNAME}
RUN adduser --disabled-password --gecos '' --uid ${USER_ID} --gid ${GROUP_ID} ${USERNAME}

USER ${USER_ID}

WORKDIR /opt/openeo_argoworkflows_api

CMD ["uvicorn", "openeo_argoworkflows_api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "8"]
