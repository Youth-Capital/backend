"""Notification dispatch."""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.common.enums import Priority

from .models import Channel, Notification, NotificationPreference

logger = logging.getLogger(__name__)


def notify(
    *,
    user,
    type: str,
    title_key: str,
    body_key: str = "",
    payload: dict | None = None,
    ref_type: str = "",
    ref_id=None,
    action_url: str = "",
    priority: str = Priority.MEDIUM,
    channel: str = Channel.IN_APP,
) -> Notification | None:
    """Create a notification, honouring the user's preferences.

    Returns None when the user has switched this type off. Failures are logged
    rather than raised — a notification is never worth failing a domain action.
    """
    try:
        preference = NotificationPreference.objects.filter(user=user, type=type).first()
        if preference is not None and not preference.enabled:
            return None

        return Notification.objects.create(
            user=user,
            type=type,
            title_key=title_key,
            body_key=body_key,
            payload=payload or {},
            ref_type=ref_type,
            ref_id=ref_id,
            action_url=action_url,
            priority=priority,
            channel=channel,
            delivered_at=timezone.now() if channel == Channel.IN_APP else None,
        )
    except Exception:  # pragma: no cover
        logger.exception("Failed to create notification type=%s for user=%s", type, user.id)
        return None


def notify_many(users, **kwargs) -> int:
    count = 0
    for user in users:
        if notify(user=user, **kwargs) is not None:
            count += 1
    return count


def mark_read(user, notification_ids: list | None = None) -> int:
    queryset = Notification.objects.filter(user=user, is_read=False)
    if notification_ids:
        queryset = queryset.filter(id__in=notification_ids)
    return queryset.update(is_read=True, read_at=timezone.now())


def unread_count(user) -> int:
    return Notification.objects.filter(user=user, is_read=False).count()
