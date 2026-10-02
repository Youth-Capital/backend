# Deployment: nginx and gunicorn

The arrangement these files describe:

```
Internet  ──TLS──>  nginx  ──127.0.0.1:8000──>  gunicorn  ──>  Django
                      │
                      ├── serves the built SPA and /static/
                      └── serves /media/ (public uploads only)
```

nginx is the only process on a public port. gunicorn listens on the loopback
interface, which is what makes the forwarded headers below trustworthy.

## Files

| File | Install to |
|---|---|
| `nginx/youth-capital.conf` | `/etc/nginx/sites-available/youth-capital.conf`, symlinked from `sites-enabled/` (or `/etc/nginx/conf.d/`) |
| `nginx/snippets/*.conf` | `/etc/nginx/snippets/` — the site file includes them by that path |
| `gunicorn/gunicorn.conf.py` | stays in the checkout; the service references it |
| `systemd/youth-capital.service` | `/etc/systemd/system/youth-capital.service` |

## Placeholders

Replace before `nginx -t`:

| Placeholder | Meaning | Example |
|---|---|---|
| `__DOMAIN__` | public hostname | `youthcapital.uz` |
| `__FRONTEND_ROOT__` | directory holding the contents of `frontend/dist` | `/srv/youth-capital/frontend` |
| `__BACKEND_DIR__` | the backend checkout | `/srv/youth-capital/backend` |
| `__RUN_USER__` | unprivileged account that owns the checkout | `youthcapital` |

```bash
cd /srv/youth-capital/backend/deploy
sed -i 's|__DOMAIN__|youthcapital.uz|g; s|__FRONTEND_ROOT__|/srv/youth-capital/frontend|g; s|__BACKEND_DIR__|/srv/youth-capital/backend|g; s|__RUN_USER__|youthcapital|g' \
    nginx/youth-capital.conf systemd/youth-capital.service
```

## Install

```bash
# 1. certificate (nginx must already answer on port 80 for the challenge)
mkdir -p /var/www/certbot
certbot certonly --webroot -w /var/www/certbot -d youthcapital.uz

# 2. nginx — check the version first: `http2 on;` needs 1.25.1 or newer, and
#    the site file says what to write instead on older ones
nginx -v
cp nginx/snippets/*.conf /etc/nginx/snippets/
cp nginx/youth-capital.conf /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/youth-capital.conf /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default      # the default site answers for any Host
nginx -t && systemctl reload nginx

# 3. Redis — where the rate limits are counted, shared by all four workers.
#    Loopback only; if it is not on this host, use rediss:// and a password.
apt install redis-server
sed -i 's/^# *bind .*/bind 127.0.0.1 -::1/' /etc/redis/redis.conf
systemctl enable --now redis-server
redis-cli ping                            # PONG
#    then put REDIS_URL=redis://127.0.0.1:6379/0 in the backend's .env
#    (production refuses to start without it) and install the client:
#    .venv/bin/pip install -r requirements/prod.txt

# 4. the application
cp systemd/youth-capital.service /etc/systemd/system/
chown youthcapital:youthcapital /srv/youth-capital/backend/.env
chmod 600 /srv/youth-capital/backend/.env
systemctl daemon-reload
systemctl enable --now youth-capital
systemctl status youth-capital

# 5. the firewall: nothing but HTTP, HTTPS and ssh
ufw allow 22,80,443/tcp
ufw enable
ufw status verbose        # ports 8000 and 6379 must NOT appear
```

## Verify

Run these from a machine that is not the server. Every one of them is a check
that something specific in these files is doing its job.

```bash
D=youthcapital.uz

# 1. gunicorn is not reachable except through nginx
curl -sS --max-time 5 http://$D:8000/api/v1/health/ ; echo
#    expected: connection refused or timeout — never a JSON response

# 2. a forged client address does not reach the application.
#    Sign in twice with a wrong password, changing the header each time, then
#    look at the recorded address in the admin under "audit". Both entries
#    must show your real address, not 10.0.0.1 or 10.0.0.2.
for ip in 10.0.0.1 10.0.0.2; do
  curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://$D/api/v1/auth/login/ \
    -H "Content-Type: application/json" -H "X-Forwarded-For: $ip" \
    -H "X-Requested-With: XMLHttpRequest" \
    -d '{"email":"nobody@example.invalid","password":"wrong-on-purpose"}'
done

# 3. the login flood stop answers 429, and changing X-Forwarded-For does not
#    get around it
for i in $(seq 1 40); do
  curl -sS -o /dev/null -w "%{http_code} " -X POST https://$D/api/v1/auth/login/ \
    -H "Content-Type: application/json" -H "X-Forwarded-For: 9.9.9.$i" \
    -H "X-Requested-With: XMLHttpRequest" \
    -d '{"email":"nobody@example.invalid","password":"wrong-on-purpose"}'
done; echo
#    expected: 400s or 401s at first, then 429 — not 400s all the way

# 4. the private upload root is not served
curl -sS -o /dev/null -w "%{http_code}\n" https://$D/private-media/
curl -sS -o /dev/null -w "%{http_code}\n" https://$D/.env
#    expected: 404 and 404

# 5. request size: small where nothing is uploaded, large where books are
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://$D/api/v1/auth/login/ \
     -H "Content-Type: application/json" --data-binary @<(head -c 200000 /dev/zero | tr '\0' 'a')
#    expected: 413

#    and real uploads through the interface must succeed — attach a 20-40 MB
#    PDF to a course as an employer, then a lesson video of a few hundred MB,
#    and watch for 413 (too small a limit) or 504 (gunicorn's timeout)

# 6. security headers on the application shell
curl -sSI https://$D/ | grep -iE 'content-security-policy|strict-transport|x-content-type|x-frame'
#    expected: all four present, CSP with a sha256- hash in script-src

# 7. headers are not duplicated on API responses (Django sends its own)
curl -sSI https://$D/api/v1/health/ | grep -ci 'strict-transport-security'
#    expected: 1

# 8. the rate limits are one limit, not one per worker. Trip the sign-in
#    limit, then keep going: every request lands on whichever of the four
#    workers is free, so a limit that is still refused after a dozen more
#    attempts is a shared one.
for i in $(seq 1 20); do
  curl -sS -o /dev/null -w "%{http_code} " -X POST https://$D/api/v1/auth/login/ \
    -H "Content-Type: application/json" -H "X-Requested-With: XMLHttpRequest" \
    -d '{"email":"nobody@example.invalid","password":"wrong-on-purpose"}'
done; echo
#    expected: 429 from some point on and not alternating back to 400/401
#    on the server: redis-cli --scan --pattern 'yc*throttle*' lists the keys

# 9. the site still works: sign in through the browser, open a lesson with a
#    video, open a course material link, look at a page with charts. A CSP
#    that blocks something shows up in the browser console, not in the logs.
```

## What this step changes

Two problems from the proxy audit are closed by `X-Forwarded-For $remote_addr`
alone, with no change to Django:

- **the rate-limit bypass.** DRF derives its throttle key from the forwarded
  header. When the header was whatever the client sent, changing one character
  produced a fresh bucket and the 5-per-minute sign-in limit never applied.
  nginx now overwrites it, so there is one value and the client does not
  choose it.
- **forged addresses in the audit log and the new-device emails.** Both read
  the first entry of that header.

Also closed: direct access to gunicorn, the private root being reachable by
path, the 1 MB default that would have refused every course book, and the SPA
being served with no CSP at all.

## The Django half, and why it depends on this file

Done in the step after this one, and only sound in company with the overwrite
above:

- **`TRUSTED_PROXY_COUNT = 1`** in production. One number, read by everything
  that decides who a request came from: DRF's rate limits (`NUM_PROXIES`), the
  lockout after failed sign-ins (`AXES_CLIENT_IP_CALLABLE`), the audit log and
  the new-device emails. All of them now count one entry in from the
  right-hand end of `X-Forwarded-For` — which is the entry nginx writes, and
  is why that has to be an overwrite and not an append.
- **Redis is required in production.** The rate limits are counted in Django's
  cache; an in-process cache gave each of the four workers its own count of
  five. `REDIS_URL` must be set or the service refuses to start.
- **The lockout is per person again.** While axes read `REMOTE_ADDR` it saw
  nginx for every visitor, which made its lockout one on the username alone:
  five wrong passwords from anywhere locked that account for half an hour.

If this nginx configuration is ever replaced by one that appends to
`X-Forwarded-For` instead of overwriting it, nothing breaks and nothing
complains — the right-hand entry is still nginx's — but if it is replaced by
one that does not set the header at all, `TRUSTED_PROXY_COUNT` must go back to
0 in the same change.

## Request-size limits, and where they come from

nginx's own default is 1 MB, so every limit below had to be set explicitly.
Each one mirrors a number inside the application; when one moves, the other
has to move with it.

| Path | nginx limit | Application's own cap |
|---|---|---|
| `/api/v1/auth/(login\|register\|password\|email/verify)/` | 64 KB | — (JSON only) |
| `/api/v1/learning/materials/` | 520 MB | `MAX_VIDEO_SIZE_MB` = 500 |
| `/api/v1/(learning\|cv\|experience\|me)/` | 45 MB | `MAX_BOOK_SIZE_MB` = 40, images 5 MB |
| everything else | 2 MB | `DATA_UPLOAD_MAX_MEMORY_SIZE` = 10 MB (JSON) |

Two practical notes on the 520 MB one: the server needs free disk under
nginx's `client_body_temp_path` (`/var/lib/nginx/body` on Debian and Ubuntu)
for each upload in flight, and gunicorn's 60-second `timeout` has to cover
Django writing that file to its final location. A 504 on a large video means
that timeout, not the nginx limits.

## Two things that will bite later

**The CSP script hash.** `snippets/youth-capital-security-headers.conf`
contains a sha256 hash of the inline theme script in `frontend/index.html`. If
that script is edited, the hash goes stale and **the page renders blank** —
the script is blocked, and the module script with it. The file carries the
one-line command that recomputes it. Worth adding to the frontend release
checklist.

**An external proxy in front of nginx.** If the site is ever put behind
Cloudflare or a load balancer, `$remote_addr` becomes *that* proxy, and the
real visitor arrives in a header again (`CF-Connecting-IP` for Cloudflare).
The proxy snippet currently clears those headers, which is right while nginx
is the edge and wrong the moment it is not. Revisit this file before making
that change, not after.
