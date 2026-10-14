"""Gunicorn for Docker; host port 8000 is published on loopback for Caddy."""

import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "sync"
threads = 1
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "300"))
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
worker_tmp_dir = "/tmp"
control_socket_disable = True

# Host-installed Caddy reaches the loopback-published Docker port and overwrites
# forwarded headers. Django uses them with TRUSTED_PROXY_COUNT=1.
forwarded_allow_ips = "*"
secure_scheme_headers = {"X-FORWARDED-PROTO": "https"}
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
accesslog = "-"
errorlog = "-"
loglevel = "info"
# Do not put signed private download links or query strings in access logs.
access_log_format = '%({x-forwarded-for}i)s "%(m)s %(U)s" %(s)s %(b)s %(M)sms'
preload_app = False
