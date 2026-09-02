"""Cross-cutting request middleware."""

import uuid

from .context import request_id_var


class RequestIDMiddleware:
    """Attach a request id to every request, log line and response.

    Lets a user-reported error be traced through the log without guessing which
    of a hundred concurrent requests it was.
    """

    HEADER = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(self.HEADER, "")
        # Never trust a client-supplied id verbatim — it ends up in log lines.
        request_id = incoming if _is_safe_id(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response[self.HEADER] = request_id
        return response


def _is_safe_id(value: str) -> bool:
    return bool(value) and len(value) <= 64 and value.replace("-", "").isalnum()
