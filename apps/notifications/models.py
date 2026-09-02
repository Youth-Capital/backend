"""Notification centre (TZ §6 "Notification center", epic E05)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.enums import Priority
from apps.common.models import BaseModel


class NotificationType(models.TextChoices):
    TASK_DUE = "TASK_DUE", _("Task due")
    TASK_OVERDUE = "TASK_OVERDUE", _("Task overdue")
    PLAN_READY = "PLAN_READY", _("Development plan ready")
    COURSE_COMPLETED = "COURSE_COMPLETED", _("Course completed")
    TEST_GRADED = "TEST_GRADED", _("Test graded")
    SKILL_VERIFIED = "SKILL_VERIFIED", _("Skill verified")
    NEW_MATCH = "NEW_MATCH", _("New matching vacancy")
    APPLICATION_STATUS = "APPLICATION_STATUS", _("Application status changed")
    NEW_APPLICATION = "NEW_APPLICATION", _("New application received")
    INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED", _("Interview scheduled")
    MENTOR_REQUEST = "MENTOR_REQUEST", _("Mentor session requested")
    MENTOR_RESPONSE = "MENTOR_RESPONSE", _("Mentor responded")
    MODERATION_RESULT = "MODERATION_RESULT", _("Moderation decision")
    SYSTEM = "SYSTEM", _("System message")


class Channel(models.TextChoices):
    IN_APP = "IN_APP", _("In app")
    EMAIL = "EMAIL", _("Email")
    SMS = "SMS", _("SMS")
    PUSH = "PUSH", _("Push")
    TELEGRAM = "TELEGRAM", _("Telegram")


class Notification(BaseModel):
    """Body is stored as a translation key plus payload, not a rendered string.

    A notification written in Uzbek today should still read correctly after the
    user switches the interface to Russian (TZ §12).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    type = models.CharField(max_length=24, choices=NotificationType.choices)
    title_key = models.CharField(max_length=80)
    body_key = models.CharField(max_length=80, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.UUIDField(null=True, blank=True)
    action_url = models.CharField(max_length=255, blank=True)

    channel = models.CharField(
        max_length=10, choices=Channel.choices, default=Channel.IN_APP
    )
    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.MEDIUM
    )
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications_notification"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_read", "-created_at"]),
            models.Index(fields=["type", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.type} -> {self.user_id}"


class NotificationPreference(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    type = models.CharField(max_length=24, choices=NotificationType.choices)
    channels = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        db_table = "notifications_preference"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "type"], name="uniq_notification_preference"
            )
        ]
