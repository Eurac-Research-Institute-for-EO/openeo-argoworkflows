ARG VARIANT="3.11-bookworm"
ARG MICROMAMBA_VERSION="2.3.3"
FROM mambaorg/micromamba:${MICROMAMBA_VERSION} AS micromamba

FROM mcr.microsoft.com/devcontainers/python:1-${VARIANT} as devcontainer

ENV POETRY_VIRTUALENVS_IN_PROJECT 1
ENV POETRY_EXPERIMENTAL_SYSTEM_GIT_CLIENT 1
ENV MAMBA_ROOT_PREFIX=/opt/micromamba
ENV GDAL_ENV=$MAMBA_ROOT_PREFIX/envs/gdal-env
ENV PATH=$GDAL_ENV/bin:$PATH
ENV LD_LIBRARY_PATH=$GDAL_ENV/lib
ENV CPLUS_INCLUDE_PATH=$GDAL_ENV/include
ENV LIBRARY_PATH=$GDAL_ENV/lib
ENV GDAL_DATA=$GDAL_ENV/share/gdal
ENV PROJ_DATA=$GDAL_ENV/share/proj

COPY --from=micromamba /usr/bin/micromamba /usr/local/bin/micromamba

RUN micromamba create -y -p $GDAL_ENV --override-channels -c conda-forge \
        "gdal=3.13.1" && \
    micromamba clean -a -y

RUN rm -f /etc/apt/sources.list.d/yarn.list && \
    apt update -qy && \
    apt install -qy --no-install-recommends \
        git

ARG POETRY_VERSION="1.8.3"
RUN if [ "${POETRY_VERSION}" != "none" ]; then su vscode -c "umask 0002 && pip3 install poetry==${POETRY_VERSION}"; fi

WORKDIR /openeo_argoworkflows_executor
