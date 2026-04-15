ARG VARIANT="3.11-bookworm"
FROM mcr.microsoft.com/devcontainers/python:1-${VARIANT} as devcontainer

ENV POETRY_VIRTUALENVS_IN_PROJECT 1

# Poetry
ARG POETRY_VERSION="1.8.3"
RUN if [ "${POETRY_VERSION}" != "none" ]; then su vscode -c "umask 0002 && pip3 install poetry==${POETRY_VERSION}"; fi

RUN rm -f /etc/apt/sources.list.d/yarn.list && \
    apt update -qy && \
    apt install -qy --no-install-recommends \
        git \
        libgdal-dev \
        gdal-bin \
        openssh-client

RUN mkdir -p /home/vscode/.ssh && \
    ssh-keyscan -t rsa,ecdsa,ed25519 github.com >> /home/vscode/.ssh/known_hosts && \
    chown -R vscode:vscode /home/vscode/.ssh && \
    chmod 700 /home/vscode/.ssh && \
    chmod 600 /home/vscode/.ssh/known_hosts

WORKDIR /openeo_argoworkflows_executor