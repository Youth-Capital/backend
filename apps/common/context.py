"""Per-request context, propagated without threading arguments everywhere."""

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

#: Actor and client metadata for audit logging, set by AuditContextMiddleware.
actor_var: ContextVar[object | None] = ContextVar("audit_actor", default=None)
client_ip_var: ContextVar[str] = ContextVar("client_ip", default="")
user_agent_var: ContextVar[str] = ContextVar("user_agent", default="")


def get_client_ip(request) -> str:
    """Best-effort client IP.

    X-Forwarded-For is only consulted because the app runs behind a trusted
    reverse proxy in production; the left-most entry is the original client.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.META.get("REMOTE_ADDR") or "")[:45]
