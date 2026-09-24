"""Configuration that must never reach a real deployment.

Django runs these on every `manage.py` command, `runserver` and `migrate`
included, so a misconfigured server stops at start-up with a named reason
rather than running and quietly giving plans away.
"""

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security)
def check_manual_auto_confirm(app_configs, **kwargs):
    """Auto-confirming manual payments is a demo convenience, never a mode.

    With it on, the manual provider settles every checkout as paid without any
    money moving: a paid plan becomes a button anybody can press. It is allowed
    only where DEBUG is on — a developer's machine — and refused everywhere
    else, including a staging box that happens to run without the production
    settings module.
    """
    enabled = bool(getattr(settings, "BILLING_MANUAL_AUTO_CONFIRM", False))
    if enabled and not settings.DEBUG:
        return [
            Error(
                "BILLING_MANUAL_AUTO_CONFIRM is on while DEBUG is off.",
                hint=(
                    "Every manual checkout would be confirmed without payment. "
                    "Remove BILLING_MANUAL_AUTO_CONFIRM from this environment."
                ),
                id="billing.E001",
            )
        ]
    return []
