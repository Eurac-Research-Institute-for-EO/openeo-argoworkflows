FROM python:3.11-slim-bookworm AS base

WORKDIR /opt
ARG POETRY_VERSION="1.8.3"

ENV POETRY_VIRTUALENVS_IN_PROJECT=1
ENV POETRY_HOME=/opt/poetry
ENV VIRTUAL_ENV=/opt/openeo_argoworkflows_executor/.venv

COPY ./README.md ./pyproject.toml ./poetry.lock /opt/

RUN python3 -m venv $VIRTUAL_ENV && \
    $VIRTUAL_ENV/bin/pip install -U pip setuptools && \
    $VIRTUAL_ENV/bin/pip install poetry==${POETRY_VERSION}

ENV PATH="${POETRY_HOME}/bin:${VIRTUAL_ENV}/bin:${PATH}"

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/* \
    && git config --global url."https://github.com/".insteadOf "git@github.com:"

RUN poetry install --only main

# Create the openeo_executor CLI entry point script directly.
# Avoids pip install . which triggers poetry-core's path validation bug.
RUN printf '#!/opt/openeo_argoworkflows_executor/.venv/bin/python\nfrom openeo_argoworkflows_executor.cli import cli\ncli()\n' > $VIRTUAL_ENV/bin/openeo_executor && \
    chmod +x $VIRTUAL_ENV/bin/openeo_executor

# Install raster2stac separately to avoid pystac version conflict with openeo-processes-dask
RUN $VIRTUAL_ENV/bin/pip install dask fsspec h5netcdf h5py kerchunk netcdf4 numcodecs "numpy<2" \
        pandas pydantic rasterio rio-stac rioxarray s3fs ujson xarray zarr requests && \
    $VIRTUAL_ENV/bin/pip install --no-deps raster2stac

RUN which openeo_executor

########################################################################################################################
FROM python:3.11-slim-bookworm AS prod

ARG USER_ID=1000
ARG GROUP_ID=1000

WORKDIR /opt/openeo_argoworkflows_executor

COPY --from=base --chown=${USER_ID}:${GROUP_ID} /opt/openeo_argoworkflows_executor/.venv /opt/openeo_argoworkflows_executor/.venv
COPY --chown=${USER_ID}:${GROUP_ID} ./openeo_argoworkflows_executor /opt/openeo_argoworkflows_executor

RUN apt-get update && apt-get install -y libexpat1 nodejs && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/local/bin/python /opt/openeo_argoworkflows_executor/.venv/bin/python \
    && ln -sf /usr/local/bin/python3 /opt/openeo_argoworkflows_executor/.venv/bin/python3 \
    && groupadd -g ${GROUP_ID} -o executor \
    && useradd -m -u ${USER_ID} -g ${GROUP_ID} -o -s /bin/bash executor

ENV VIRTUAL_ENV=/opt/openeo_argoworkflows_executor/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV DASK_GATEWAY_SERVER="http://traefik-openeo-dask-gateway.openeo.svc.cluster.local:80"
ENV PYTHONPATH="/opt"

RUN mkdir -p /mainctrfs && ln -s /user_workspaces /mainctrfs/user_workspaces

USER ${USER_ID}

CMD ["openeo_executor"]