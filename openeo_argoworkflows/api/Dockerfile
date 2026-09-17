FROM python:3.11-slim AS base

WORKDIR /opt

ARG POETRY_VERSION="1.8.3"

ENV POETRY_VIRTUALENVS_IN_PROJECT=1
ENV POETRY_HOME=/opt/poetry
ENV VIRTUAL_ENV=/opt/openeo_argoworkflows_api/.venv
ENV PATH="${POETRY_HOME}/bin:${VIRTUAL_ENV}/bin:${PATH}"

COPY ./README.md ./pyproject.toml ./poetry.lock /opt/

RUN python3 -m venv $VIRTUAL_ENV && \
    $VIRTUAL_ENV/bin/pip install -U pip setuptools && \
    $VIRTUAL_ENV/bin/pip install poetry==${POETRY_VERSION}

# ------------------------------------------------------------------
# test — install dev dependencies & PostgreSQL, run the test suite
# ------------------------------------------------------------------
FROM base AS test

ARG USER_ID=1000
ARG GROUP_ID=1000

ENV USERNAME=api

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql \
    postgresql-client \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN poetry install --all-extras

COPY ./openeo_argoworkflows_api /opt/openeo_argoworkflows_api
COPY ./tests /opt/tests
COPY ./.devcontainer /opt/.devcontainer

# pytest-postgresql needs the pg_ctl executable; default location
ENV PATH="/usr/lib/postgresql/*/bin:${PATH}"

RUN addgroup --gid ${GROUP_ID} ${USERNAME} && \
    adduser --disabled-password --gecos '' --uid ${USER_ID} --gid ${GROUP_ID} ${USERNAME} && \
    chown -R ${USER_ID}:${GROUP_ID} /opt/tests /opt

USER ${USER_ID}

WORKDIR /opt
CMD ["python", "-m", "pytest", "tests/", "-x", "-v", "--no-header"]

# ------------------------------------------------------------------
# prod — minimal production image
# ------------------------------------------------------------------
FROM base AS prod

ARG USER_ID=1000
ARG GROUP_ID=1000

ENV USERNAME=api

RUN poetry install --only main

COPY --chown=${USER_ID}:${GROUP_ID} ./openeo_argoworkflows_api /opt/openeo_argoworkflows_api

RUN addgroup --gid ${GROUP_ID} ${USERNAME} && \
    adduser --disabled-password --gecos '' --uid ${USER_ID} --gid ${GROUP_ID} ${USERNAME}

USER ${USER_ID}

WORKDIR /opt/openeo_argoworkflows_api

CMD ["uvicorn", "openeo_argoworkflows_api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "8"]
