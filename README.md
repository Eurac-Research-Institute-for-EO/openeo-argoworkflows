# OpenEO ArgoWorkflows

OpenEO Argoworkflows is an implementation of the [OpenEO Api]() and [OpenEO Processes]() specifications. This repository implements two components, an api server, and an executor. The Api implementation is based on the [OpenEO Fastapi]() package, and the Executor implementation is based on [OpenEO Processes Dask]() and [OpenEO PG Parser]().

The two components here, are integrated, and expected to be installed via the [OpenEO ArgoWorkflows Helm Chart](). The helm chart a number of dependencies are installed and configured to work with the components implemented here.


## Development

In the respective documentary for the api and executor there is a dockerfile defined that can be used as a development environment for each component.

To work on the Api
```
cd ./openeo_argoworkflows/api
```

To work on the Executor
```
cd ./openeo_argoworkflows/api
```

From each of these directories, there is a .devcontainer configuration. Openining these sub directories in vscode will display the option to open the development container. It is intentional that each of these components have seperate development environments. Source and test code is available in each respective sub repo. **Note**: there are currently no tests for the executor.

## Release

Each component is released using the version number found in its respective pyproject.toml, and prefixed with the name of the component. The version numbers in the toml follow [Calendar Versioning](https://calver.org/) where the Major version is the year, the Minor version the month, and the Mirco version is the index of the release that month, if it is the first release that month, the Micro is 1.

Format:
`( api | executor )-YYYY.MM.MICRO`s

## Dependency & Tag Map

This repository depends on several EURAC-maintained packages.
Below is a summary of which tags/revisions are currently referenced, where they are defined, and how they are consumed.

### Direct Dependencies (in `pyproject.toml`)

| Package | Reference | Defined in |
|---|---|---|
| `openeo-processes-dask` | `tag = "v2026.7.1-eurac-dev.1"` | `openeo_argoworkflows/executor/pyproject.toml` |
| `openeo-processes-save-result` | `tag = "v2026.7.1-eurac-dev.2"` | `openeo_argoworkflows/executor/pyproject.toml` |
| `openeo-pg-parser-networkx` | `tag = "v2026.7.1-eurac-dev.1"` | `openeo_argoworkflows/executor/pyproject.toml` |

### Transitive Dependency: `openeo-python-client`

`openeo-processes-save-result` declares `openeo-python-client` as a dependency in its own `pyproject.toml`. The version is resolved transitively through `poetry.lock`.

| Package | Tag | Defined in |
|---|---|---|
| `openeo-python-client` | `v2026.7.1-eurac-dev.2` | `openeo-processes-save-result/pyproject.toml` (resolved in `poetry.lock`) |

### Docker Image Tags

Two Docker images are built and pushed to GHCR via GitHub Actions workflows.

#### API Image (`ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-api`)

| Branch | Tags pushed | Workflow |
|---|---|---|
| `dev` | `dev`, `sha-<commit>` | `.github/workflows/build-api.yaml` |
| `eurac-main` | `latest`, `eurac-main`, `stable`, `sha-<commit>` | `.github/workflows/build-api.yaml` |
| `main` | `sha-<commit>` | `.github/workflows/build-api.yaml` |
| Tag `api-*.*.*` | semver tags | `.github/workflows/release.yaml` |

#### Executor Image (`ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-executor`)

| Branch | Tags pushed | Workflow |
|---|---|---|
| `dev` | `dev`, `sha-<commit>` | `.github/workflows/build-executor.yaml` |
| `eurac-main` | `latest`, `eurac-main`, `stable`, `sha-<commit>` | `.github/workflows/build-executor.yaml` |
| `main` | `sha-<commit>` | `.github/workflows/build-executor.yaml` |
| Tag `executor-*.*.*` | semver tags | `.github/workflows/release.yaml` |

### How Images are Built

- **API image**: Built from `openeo_argoworkflows/api/Dockerfile`, triggered on pushes to `dev`/`eurac-main`/`main` when `openeo_argoworkflows/api/**` changes.
- **Executor image**: Built from `Dockerfile.executor` (repo root), triggered on pushes to `dev`/`eurac-main`/`main` when `Dockerfile.executor` or `openeo_argoworkflows/executor/**` changes.
- The executor Dockerfile uses `poetry export` to freeze dependencies into a `requirements.txt`, then `pip install`s them. `openeo-processes-dask` and `openeo-processes-save-result` are installed separately with `--no-deps` to avoid pulling their transitive dependencies twice.

### Hardcoded Refs in Dockerfile.executor

The executor Dockerfile (`Dockerfile.executor` at repo root) has two hardcoded git references that are NOT managed by poetry:

```dockerfile
"openeo-processes-dask @ git+https://github.com/...@v2026.7.1-eurac-dev.1"
"openeo-processes-save-result @ git+https://github.com/...@v2026.7.1-eurac-dev.1"
```

These are listed in the `pyproject.toml` as regular poetry dependencies. However, the Dockerfile adds them as `--no-deps` pip installs because their transitive dependencies (except `gdal`) are already included in the poetry export. **When updating these versions, make sure both `pyproject.toml` and `Dockerfile.executor` are updated consistently.**

### CI Workflows

| Workflow | Triggers | What it does |
|---|---|---|
| `build-api.yaml` | Push to `dev`/`eurac-main`/`main` + API path changes | Builds & pushes API image to GHCR |
| `build-executor.yaml` | Push to `dev`/`eurac-main`/`main` + executor path changes | Builds & pushes executor image to GHCR |
| `ci-executor-image.yaml` | Push/PR to `dev`/`eurac-main`/`feature/**` + executor changes | CI-only: builds image, runs smoke tests and pytest (does NOT push) |
| `main.yaml` | Push/PR to `main`/`eurac-main` | Runs pytest via devcontainer (not triggered on `dev`) |
| `release.yaml` | Tag `api-*.*.*` or `executor-*.*.*` | Builds & pushes release images to GHCR |
| `save-result-bridge.yaml` | Weekly schedule or manual dispatch | Integration test with openeo-processes-save-result |

### Deployment

Deployment is managed via a separate Helm chart repository: https://github.com/Eurac-Research-Institute-for-EO/charts.git

The Helm chart `values.yaml` references the Docker images:

```yaml
executorImage: ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-executor:latest
image: ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-api:dev
```

An in-house override file is available at `deploy/values-inhouse.yaml`.

### Updating Dependencies

When updating a dependency tag (e.g. bumping `openeo-processes-save-result`):

1. Update the tag in `openeo_argoworkflows/executor/pyproject.toml`
2. Update the matching `pip install` line in `Dockerfile.executor` if the tag appears there
3. Run `poetry lock` in `openeo_argoworkflows/executor/` to regenerate `poetry.lock`
4. Commit, push, and create a PR targeting `dev`
5. After merge to `dev`, CI builds and pushes new Docker images automatically
