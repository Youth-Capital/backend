# Production deployment

The production runbook, Docker Compose stack, HTTPS/HTTP3 proxy and Ubuntu
storage preparation are maintained in [Youth-Capital/infra](https://github.com/Youth-Capital/infra).
Only the infra repository connects to the server.

The dedicated host uses shared PostgreSQL with the verified application database
`yoshlar_kapitali_db` and role `yoshlar_kapitali_user`. Production reads `DB_HOST`,
`DB_PORT`, `DB_NAME`, `DB_USER` and the application-role `POSTGRES_PASSWORD`.
The infra renderer also supplies `DATABASE_URL` for older images. Explicit DB
settings take precedence; existing URL/POSTGRES_* development settings remain
supported. No PostgreSQL administrator password belongs in application runtime.
The existing shared Caddy edge serves the domain; follow infra's rollout runbook
for data transfer, Cloudflare Full (strict), and HTTP-01 issuance. Starting the
backend still runs migrations, so deployment requires operator approval.
For a source-only push to main, include `[no deploy]` in the head commit message.
Security checks, tests, image publication and image scanning still run; the
automatic infra promotion is held. Approved manual releases retain the existing
`publish_only` workflow input.

This repository builds its production image with the root `Dockerfile` and
`entrypoint.sh`. Python packages are managed by **uv 0.13.0** using `pyproject.toml`
and `uv.lock`; use `uv sync --locked` for development, `uv run pytest` for tests,
and `uv sync --locked --no-dev` for a production dependency installation.
See the [backend README](../README.md) for application runtime details.

The nginx and systemd files in this directory are legacy host deployment
examples. They are not used by the current CI/CD pipeline or Compose deployment.
Do not install them alongside the infra stack. `gunicorn/gunicorn.conf.py` is
current and is copied into the backend Docker image.
