"""django-axes lockout response.

Without this, axes returns an HTML page; the SPA needs the same JSON error
envelope every other failure uses.
"""

from django.conf import settings
from django.http import JsonResponse

from apps.common.context import request_id_var


def lockout_response(request, credentials=None, *args, **kwargs):
    minutes = int(settings.AXES_COOLOFF_TIME.total_seconds() // 60)
    return JsonResponse(
        {
            "error": {
                "code": "account_locked",
                "message": (
                    f"Too many failed sign-in attempts. Try again in {minutes} minutes."
                ),
                "details": {"cooloff_minutes": minutes},
                "request_id": request_id_var.get(),
            }
        },
        status=429,
    )
