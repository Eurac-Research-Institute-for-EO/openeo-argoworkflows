ARG VARIANT="3.11-bookworm"
FROM mcr.microsoft.com/devcontainers/python:1-${VARIANT} as devcontainer

ENV POETRY_VIRTUALENVS_IN_PROJECT 1
ENV POETRY_EXPERIMENTAL_SYSTEM_GIT_CLIENT 1

# Poetry
ARG POETRY_VERSION="1.8.3"
RUN if [ "${POETRY_VERSION}" != "none" ]; then su vscode -c "umask 0002 && pip3 install poetry==${POETRY_VERSION}"; fi

RUN rm -f /etc/apt/sources.list.d/yarn.list && \
    apt update -qy && \
    apt install -qy --no-install-recommends \
        git \
        libgdal-dev \
        gdal-bin

WORKDIR /openeo_argoworkflows_executor