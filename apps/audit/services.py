"""Audit helpers."""

from __future__ import annotations

import logging
from typing import Any

from apps.common.context import (
    actor_var,
    client_ip_var,
    request_id_var,
    user_agent_var,
)

from .models import AuditAction, AuditLog, AuditSeverity

logger = logging.getLogger(__name__)

#: Never write these into an audit diff, whatever the caller passes.
REDACTED_FIELDS = frozenset(
    {"password", "token", "token_hash", "api_key", "secret", "refresh", "access"}
)


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ("***" if key.lower() in REDACTED_FIELDS else value)
        for key, value in (data or {}).items()
    }


def log_action(
    *,
    action: str,
    obj=None,
    object_type: str = "",
    object_id: str = "",
    object_repr: str = "",
    actor=None,
    before: dict | None = None,
    after: dict | None = None,
    note: str = "",
    severity: str = AuditSeverity.INFO,
) -> AuditLog | None:
    """Write one audit entry.

    Failures here are logged and swallowed: an audit write must never be the
    reason a user's action fails.
    """
    try:
        actor = actor or actor_var.get()
        if obj is not None:
            object_type = object_type or obj.__class__.__name__
            object_id = object_id or str(getattr(obj, "id", ""))
            object_repr = object_repr or str(obj)[:200]

        changes: dict[str, Any] = {}
        if before is not None or after is not None:
            changes = {"before": _redact(before or {}), "after": _redact(after or {})}

        return AuditLog.objects.create(
            actor=actor if getattr(actor, "pk", None) else None,
            actor_email=getattr(actor, "email", "") or "",
            actor_role=getattr(actor, "role", "") or "",
            action=action,
            object_type=object_type[:60],
            object_id=str(object_id)[:64],
            object_repr=object_repr[:200],
            changes=changes,
            note=note[:255],
            ip=client_ip_var.get() or None,
            user_agent=user_agent_var.get()[:400],
            request_id=request_id_var.get(),
            severity=severity,
        )
    except Exception:  # pragma: no cover - auditing must not break the request
        logger.exception("Failed to write audit log for action=%s", action)
        return None


def diff_fields(instance, updates: dict[str, Any]) -> tuple[dict, dict]:
    """Return (before, after) restricted to fields that actually changed."""
    before, after = {}, {}
    for field, new_value in updates.items():
        old_value = getattr(instance, field, None)
        if old_value != new_value:
            before[field] = _serialise(old_value)
            after[field] = _serialise(new_value)
    return before, after


def _serialise(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "pk"):
        return str(value.pk)
    return value


def log_status_change(obj, *, from_status: str, to_status: str, actor=None, note=""):
    return log_action(
        action=AuditAction.STATUS_CHANGE,
        obj=obj,
        actor=actor,
        before={"status": from_status},
        after={"status": to_status},
        note=note,
        severity=AuditSeverity.NOTICE,
    )


def log_moderation(obj, *, decision: str, actor=None, note=""):
    return log_action(
        action=AuditAction.MODERATE,
        obj=obj,
        actor=actor,
        after={"decision": decision},
        note=note,
        severity=AuditSeverity.NOTICE,
    )
