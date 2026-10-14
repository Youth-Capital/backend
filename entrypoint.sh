#!/bin/sh
set -eu
umask 027

# One-shot maintenance commands use the same image without rerunning startup.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

: "${DJANGO_SECRET_KEY:?DJANGO_SECRET_KEY is required}"
: "${DJANGO_ALLOWED_HOSTS:?DJANGO_ALLOWED_HOSTS is required}"
: "${DATABASE_URL:?DATABASE_URL is required}"
: "${REDIS_URL:?REDIS_URL is required}"
: "${FRONTEND_URL:?FRONTEND_URL is required}"
: "${CORS_ALLOWED_ORIGINS:?CORS_ALLOWED_ORIGINS is required}"
: "${EMAIL_HOST:?EMAIL_HOST is required}"
: "${EMAIL_HOST_USER:?EMAIL_HOST_USER is required}"
: "${EMAIL_HOST_PASSWORD:?EMAIL_HOST_PASSWORD is required}"
export DJANGO_SETTINGS_MODULE=config.settings.prod
export GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
export GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-300}"

python - <<'PY'
import os
import sys
import time

for name in ("GUNICORN_WORKERS", "GUNICORN_TIMEOUT", "STARTUP_TIMEOUT"):
    value = os.environ.get(name, "120")
    if not value.isascii() or not value.isdigit() or int(value) < 1:
        sys.exit(f"{name} must be a positive integer")

import django
django.setup()
from pathlib import Path
from django.conf import settings
from django.core.cache import cache
from django.db import connection

for location in (settings.MEDIA_ROOT, settings.PRIVATE_MEDIA_ROOT,
                 settings.FILE_UPLOAD_TEMP_DIR):
    directory = Path(location)
    directory.mkdir(parents=True, exist_ok=True)
    if not os.access(directory, os.W_OK):
        sys.exit(f"Upload directory is not writable: {directory}")

deadline = time.monotonic() + int(os.environ.get("STARTUP_TIMEOUT", "120"))
while True:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        cache.get("startup-probe")
        break
    except Exception as exc:
        connection.close()
        if time.monotonic() >= deadline:
            sys.exit(f"Database/cache startup check failed: {type(exc).__name__}")
        print(f"Waiting for database/cache: {type(exc).__name__}", flush=True)
        time.sleep(2)
connection.close()
PY

python manage.py check --fail-level ERROR
python manage.py check --deploy --tag security --fail-level WARNING
python manage.py migrate --noinput
exec gunicorn -c deploy/gunicorn/gunicorn.conf.py config.wsgi:application
