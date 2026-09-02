"""Audit log — read-only, admins only.

There is no write endpoint and no delete endpoint by design: a trail anyone can
edit is not a trail.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets

from apps.common.permissions import IsAdmin

from ..models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "actor_email",
            "actor_role",
            "action",
            "object_type",
            "object_id",
            "object_repr",
            "changes",
            "note",
            "ip",
            "request_id",
            "severity",
            "created_at",
        ]
        read_only_fields = fields


@extend_schema(tags=["audit"])
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdmin]
    serializer_class = AuditLogSerializer
    queryset = AuditLog.objects.select_related("actor").order_by("-created_at")
    filterset_fields = ["action", "object_type", "severity", "actor"]
    search_fields = ["object_repr", "actor_email", "note"]
