"""Audit trail (TZ §12 "Auditability", epic E17)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel


class AuditAction(models.TextChoices):
    CREATE = "CREATE", _("Create")
    UPDATE = "UPDATE", _("Update")
    DELETE = "DELETE", _("Delete")
    LOGIN = "LOGIN", _("Login")
    LOGIN_FAILED = "LOGIN_FAILED", _("Failed login")
    LOGOUT = "LOGOUT", _("Logout")
    PASSWORD_CHANGE = "PASSWORD_CHANGE", _("Password change")
    ROLE_CHANGE = "ROLE_CHANGE", _("Role change")
    MODERATE = "MODERATE", _("Moderation decision")
    STATUS_CHANGE = "STATUS_CHANGE", _("Status change")
    CONSENT_GRANT = "CONSENT_GRANT", _("Consent granted")
    CONSENT_REVOKE = "CONSENT_REVOKE", _("Consent revoked")
    PII_ACCESS = "PII_ACCESS", _("Personal data accessed")
    EXPORT = "EXPORT", _("Data export")
    CONFIG_CHANGE = "CONFIG_CHANGE", _("Configuration change")


class AuditSeverity(models.TextChoices):
    INFO = "INFO", _("Info")
    NOTICE = "NOTICE", _("Notice")
    WARNING = "WARNING", _("Warning")
    CRITICAL = "CRITICAL", _("Critical")


class AuditLog(BaseModel):
    """Append-only record of consequential actions.

    Never updated or deleted through the application. `actor` is nullable
    because the row must survive the deletion of the account that caused it.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    actor_email = models.CharField(max_length=254, blank=True)
    actor_role = models.CharField(max_length=16, blank=True)

    action = models.CharField(max_length=20, choices=AuditAction.choices)
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)

    #: {"before": {...}, "after": {...}} — only the fields that changed.
    changes = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=255, blank=True)

    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    severity = models.CharField(
        max_length=8, choices=AuditSeverity.choices, default=AuditSeverity.INFO
    )

    class Meta:
        db_table = "audit_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["object_type", "object_id"]),
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
            models.Index(fields=["severity", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.object_type}#{self.object_id} by {self.actor_email or 'system'}"
