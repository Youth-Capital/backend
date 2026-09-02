"""Uniform API error envelope.

Every failure comes back as::

    {"error": {"code": "...", "message": "...", "details": {...}, "request_id": "..."}}

The frontend localises by ``code``; ``message`` is a developer-facing fallback,
never the string shown to a user. Requirement: docs/02-ARCHITECTURE.md §10.
"""

import logging

from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .context import request_id_var

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Business-rule violation raised from a service layer.

    Services raise this instead of DRF exceptions so they stay usable from
    management commands and Celery tasks where there is no request.
    """

    code = "domain_error"
    status_code = 400
    message = "Operation not allowed."

    def __init__(self, message: str | None = None, *, code: str | None = None, details=None):
        self.message = message or self.message
        self.code = code or self.code
        self.details = details or {}
        super().__init__(self.message)


class NotAllowed(DomainError):
    code = "not_allowed"
    status_code = 403
    message = "You do not have permission to perform this action."


class Conflict(DomainError):
    code = "conflict"
    status_code = 409
    message = "The resource is in a conflicting state."


_STATUS_CODES = {
    400: "bad_request",
    401: "authentication_required",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media_type",
    429: "throttled",
    500: "internal_error",
}


def api_exception_handler(exc, context):
    if isinstance(exc, DomainError):
        return _envelope(exc.status_code, exc.code, exc.message, exc.details)

    if isinstance(exc, DjangoValidationError):
        exc = drf_exceptions.ValidationError(detail=list(exc.messages))
    elif isinstance(exc, Http404):
        exc = drf_exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = drf_exceptions.PermissionDenied()

    response = drf_exception_handler(exc, context)
    if response is None:
        # Unhandled: log with traceback, but never leak internals to the client.
        logger.exception("Unhandled exception in %s", context.get("view"))
        return _envelope(500, "internal_error", "Internal server error.", {})

    code = getattr(exc, "default_code", None) or _STATUS_CODES.get(
        response.status_code, "error"
    )
    detail = response.data

    if isinstance(detail, dict) and "detail" in detail and len(detail) == 1:
        message, details = str(detail["detail"]), {}
    elif isinstance(detail, dict):
        message, details = "Validation failed.", detail
        code = "validation_error"
    elif isinstance(detail, list):
        message, details = "Validation failed.", {"non_field_errors": detail}
        code = "validation_error"
    else:
        message, details = str(detail), {}

    return _envelope(response.status_code, code, message, details, response.headers)


def _envelope(status_code, code, message, details, headers=None):
    payload = {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id_var.get(),
        }
    }
    response = Response(payload, status=status_code)
    for key, value in (headers or {}).items():
        if key.lower() != "content-type":
            response[key] = value
    return response
