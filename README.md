# Youth Capital backend

Python **3.14.7**, Django 5.2 LTS and **uv 0.13.0**. `pyproject.toml` and
`uv.lock` are the dependency source of truth; development tools live in the
`dev` group.

```sh
uv sync --locked
uv run python manage.py runserver
uv run pytest
```

CI runs tests against PostgreSQL 18 using `TEST_DATABASE_URL`; local tests
otherwise use SQLite. Install production dependencies with `uv sync --locked
--no-dev`. Update dependencies with uv and commit both dependency files.

The Docker image collects Django admin static files during its build and runs
as UID/GID `10001` with a read-only root filesystem. Mount persistent writable
volumes at `/app/media` and `/app/private-media`, and a small tmpfs at `/tmp`.
Large upload scratch files use `/app/private-media/.tmp` on disk. Publish port
8000 only on host `127.0.0.1` so the host-installed Caddy service can reach it.

`entrypoint.sh` checks configuration and dependencies, runs deployment checks
and migrations, then launches Gunicorn. `GUNICORN_WORKERS` defaults to 2;
`GUNICORN_TIMEOUT` defaults to 300 seconds. Runtime secrets belong to the infra
repository's GitHub `prod` environment and are supplied at deployment time.

Production Compose, server bootstrap, networking and the complete operator
guide live in [Youth-Capital/infra](https://github.com/Youth-Capital/infra).
The application pipeline publishes and scans an image, then updates infra;
only infra deploys to the server. The old host-level nginx/systemd examples in
`deploy` are historical references. Do not combine them with the Compose stack.

Registration sends a six-digit email OTP using the existing SMTP configuration.
Codes expire after 10 minutes, allow five failed guesses, and can be resent once
per minute with a maximum of five sends per account per hour. Only the latest
code works. The database stores a keyed hash, never a plaintext code. Delivery
uses a 10-second SMTP socket timeout and reports failure without losing the
pending account. No additional environment values or Resend SDK are required.

Pending accounts receive a session limited to identity, email verification,
resend and logout. Backend JWT authentication and signed-file access both block
application access until the address is verified. Existing unverified accounts
must verify too; no addresses are silently marked verified by migration. Old
verification links are invalidated, and debug headers never return codes or
tokens. The frontend `/auth/verify-email` screen resumes after reload/sign-in;
verified learners continue to intake if it has not been completed.

The release workflow runs three security scans, tests, build/publish, image
scanning, infra deployment and final registry cleanup. PRs receive no production
credentials. In this repo's `prod` environment, set the secret
`INFRA_APP_PRIVATE_KEY` and variables `INFRA_APP_ID` and
`INFRA_REPO=Youth-Capital/infra`. The App is installed only on infra with
Contents and Actions read/write permission. No VPS or runtime credentials
belong here.

First dispatch with `publish_only=true`, grant infra Read under the new GHCR
package's Manage Actions access, then run the full release with that option
disabled. Retrying only deploy reuses its built image, avoids empty commits,
and waits for a verified infra deployment. Failed deployments skip final
cleanup; the separate Registry maintenance workflow can remove obsolete
versions before another build. Source and image scans block HIGH/CRITICAL
findings. Current Alpine security updates are installed in the runtime stage,
which the release build refreshes without discarding the dependency cache.

Run release protocol tests with `node --test .github/scripts/release.test.mjs`
using Node 24.21.0. The [infra operator guide](https://github.com/Youth-Capital/infra/blob/main/README.md)
documents SSH account/key setup, every production setting and recovery steps.

Liveness: `/api/v1/health/`. Readiness: `/api/v1/health/ready/` (database and
cache). The proxy routes `/api/`, `/django-admin/` and `/static/` to Django.
Public media is served from `/media/`; private files go through authenticated
API download endpoints.
