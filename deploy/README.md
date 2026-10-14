# Production deployment

The production runbook, Docker Compose stack, HTTPS/HTTP3 proxy and Ubuntu
bootstrap are maintained in [Youth-Capital/infra](https://github.com/Youth-Capital/infra).
Only the infra repository connects to the server.

This repository builds its production image with the root `Dockerfile` and
`entrypoint.sh`. Python packages are managed by **uv 0.13.0** using `pyproject.toml`
and `uv.lock`; use `uv sync --locked` for development, `uv run pytest` for tests,
and `uv sync --locked --no-dev` for a production dependency installation.
See the [backend README](../README.md) for application runtime details.

The nginx and systemd files in this directory are legacy host deployment
examples. They are not used by the current CI/CD pipeline or Compose deployment.
Do not install them alongside the infra stack. `gunicorn/gunicorn.conf.py` is
current and is copied into the backend Docker image.
