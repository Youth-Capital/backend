"""Gunicorn configuration for production.

Run it with:

    .venv/bin/gunicorn -c deploy/gunicorn/gunicorn.conf.py config.wsgi:application

The one line here that is a security control rather than a tuning knob is
`bind`. Everything else can be adjusted freely.
"""

import os

# --- where it listens ------------------------------------------------------
#
# The loopback interface, so the only way in is through nginx.
#
# The README used to say `--bind 0.0.0.0:8000`, which listens on every
# interface. That matters more than it looks: Django trusts X-Forwarded-Proto
# to decide whether a request arrived over TLS, and reads the client's address
# out of X-Forwarded-For. nginx overwrites both (deploy/nginx/snippets/
# youth-capital-proxy.conf) — but a request that never passes through nginx
# sets them itself. On a public port, anyone could claim any address, land it
# in the audit log, and get a fresh rate-limit bucket per request.
#
# A unix socket is stricter still (no port to reach at all, even from another
# account on the same host). If you prefer it:
#     bind = "unix:/run/youth-capital/gunicorn.sock"
# and in nginx: `server unix:/run/youth-capital/gunicorn.sock;`
bind = "127.0.0.1:8000"

# Whose forwarded headers gunicorn itself will honour. The default is already
# the loopback address; it is spelled out because it is the setting people
# widen to "*" when debugging and then forget.
forwarded_allow_ips = "127.0.0.1"
secure_scheme_headers = {"X-FORWARDED-PROTO": "https"}

# --- workers ---------------------------------------------------------------
#
# Synchronous workers: the application does blocking database work and no
# long-lived connections.
#
# Because this many processes run, the rate limits cannot live inside one of
# them: DRF counts every throttle in Django's cache, so an in-process cache
# would mean four separate counts of "5 per minute", forgotten on restart.
# Production therefore refuses to start without REDIS_URL — see
# config/settings/prod.py. Raising the worker count needs no thought about
# the limits; lowering Redis to a local cache would silently multiply them.
workers = int(os.environ.get("WEB_CONCURRENCY", "4"))
worker_class = "sync"
threads = 1

timeout = 60
graceful_timeout = 30
keepalive = 5

# Recycle workers so a slow leak cannot accumulate. The jitter keeps all four
# from restarting in the same second.
max_requests = 1000
max_requests_jitter = 100

# --- request limits --------------------------------------------------------
#
# A second line behind nginx, in case gunicorn is ever reached directly during
# maintenance. These are gunicorn's defaults, made explicit.
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# --- logging ---------------------------------------------------------------
#
# To stdout and stderr, so systemd captures both into the journal and there is
# one place to read. Django's own file handler keeps the application log.
accesslog = "-"
errorlog = "-"
loglevel = "info"

# The client address in the access log is the one nginx forwarded, which after
# the proxy configuration in this directory is the real visitor.
access_log_format = '%({x-forwarded-for}i)s "%(r)s" %(s)s %(b)s %(M)sms "%(f)s" "%(a)s"'

# Left off on purpose: `preload_app` saves memory but changes when settings
# are imported, and every check in this project's settings modules
# (SECRET_KEY strength, the billing flag) is meant to run at worker start
# where its failure is visible in the journal.
preload_app = False
