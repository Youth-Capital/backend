"""Per-request context, propagated without threading arguments everywhere."""

from contextvars import ContextVar

from django.conf import settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

#: Actor and client metadata for audit logging, set by AuditContextMiddleware.
actor_var: ContextVar[object | None] = ContextVar("audit_actor", default=None)
client_ip_var: ContextVar[str] = ContextVar("client_ip", default="")
user_agent_var: ContextVar[str] = ContextVar("user_agent", default="")


def get_client_ip(request) -> str:
    """Who this request came from, counted from the end of the proxy chain.

    One function, because "the client's address" has to mean the same thing
    everywhere it is used: the rate limits, the lockout after repeated failed
    sign-ins, the audit log, and the address quoted in a new-device email.
    While the audit log read the first entry of `X-Forwarded-For` and the rate
    limits hashed the whole header, a visitor could be one address to one and a
    different address to the other, and choose both.

    `TRUSTED_PROXY_COUNT` says how many proxies of ours stand in front of the
    application. Each one appends what it saw to `X-Forwarded-For`, so with one
    proxy the last entry is the address nginx accepted the connection from, and
    anything to the left of it was written by the caller. Counting from the
    right is therefore the whole point: the left-hand entries are a claim, the
    right-hand ones are a record. This is deliberately the same arithmetic DRF
    uses for `NUM_PROXIES`, so the two can never disagree.

    With no trusted proxy — a developer's machine, the test suite — the header
    is ignored entirely. There is nothing in front of the application to have
    written it, so its only possible author is the caller.

    Also refused: a header with fewer entries than there are proxies. That
    cannot have come through the chain we expect, so it is not evidence of
    anything and `REMOTE_ADDR` is used instead.
    """
    trusted = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    if trusted > 0:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
        if len(entries) >= trusted:
            return entries[-trusted][:45]
    return (request.META.get("REMOTE_ADDR") or "")[:45]
