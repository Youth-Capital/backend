# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv
FROM python:3.14.7-alpine@sha256:9e9fde4d32eedce0b661d9ab91e826b62dddf28e928c230ec55f1866cac66b01 AS python-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
# pip's vendor libraries are build tooling; the isolated uv environment needs
# none. Dependencies remain cached separately from runtime security updates.
RUN python -m pip uninstall -y pip

FROM python-base AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable
COPY apps ./apps
COPY config ./config
COPY manage.py ./
RUN DJANGO_SETTINGS_MODULE=config.settings.build \
    python manage.py collectstatic --noinput

FROM python-base AS runtime
ENV DJANGO_SETTINGS_MODULE=config.settings.prod \
    GUNICORN_WORKERS=2 \
    GUNICORN_TIMEOUT=300 \
    STARTUP_TIMEOUT=120
# CI disables caching for this stage so every release gets available OS fixes.
RUN apk upgrade --no-cache \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app -s /sbin/nologin app \
    && mkdir -p /app/media /app/private-media /app/private-media/.tmp \
    && chown -R app:app /app/media /app/private-media \
    && chmod 0700 /app/private-media /app/private-media/.tmp
COPY --from=build /app/.venv ./.venv
COPY --from=build /app/apps ./apps
COPY --from=build /app/config ./config
COPY --from=build /app/staticfiles ./staticfiles
COPY --from=build /app/manage.py ./manage.py
COPY deploy/gunicorn/gunicorn.conf.py ./deploy/gunicorn/gunicorn.conf.py
COPY --chmod=0555 entrypoint.sh ./entrypoint.sh
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD ["python", "-c", "import urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/api/v1/health/ready/',headers={'Host':'localhost','X-Forwarded-Proto':'https'}); response=urllib.request.urlopen(r,timeout=8); assert response.status == 200"]
ENTRYPOINT ["/app/entrypoint.sh"]
