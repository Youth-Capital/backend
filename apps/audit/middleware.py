"""Populate the audit context for the duration of a request."""

from apps.common.context import (
    actor_var,
    client_ip_var,
    get_client_ip,
    user_agent_var,
)


class AuditContextMiddleware:
    """Make actor, IP and user agent available to services without plumbing
    the request object through every call."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip_token = client_ip_var.set(get_client_ip(request))
        ua_token = user_agent_var.set(request.headers.get("User-Agent", "")[:400])
        actor_token = actor_var.set(None)
        try:
            response = self.get_response(request)
        finally:
            actor_var.reset(actor_token)
            user_agent_var.reset(ua_token)
            client_ip_var.reset(ip_token)
        return response


def set_audit_actor(user) -> None:
    """Called from DRF views once authentication has resolved the user.

    Django's AuthenticationMiddleware runs before DRF authenticates, so
    `request.user` is still anonymous when this middleware executes.
    """
    if user is not None and getattr(user, "is_authenticated", False):
        actor_var.set(user)
