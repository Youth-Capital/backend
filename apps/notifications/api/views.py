"""Notification centre endpoints."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ..models import Notification, NotificationPreference
from ..services import mark_read, unread_count


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "type",
            "title_key",
            "body_key",
            "payload",
            "ref_type",
            "ref_id",
            "action_url",
            "priority",
            "is_read",
            "read_at",
            "created_at",
        ]
        read_only_fields = fields


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ["id", "type", "channels", "enabled"]
        read_only_fields = ["id"]


@extend_schema(tags=["notifications"])
class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer
    filterset_fields = ["type", "is_read", "priority"]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).order_by("-created_at")

    @extend_schema(responses={200: dict})
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread(self, request):
        return Response({"count": unread_count(request.user)})

    @extend_schema(request=dict, responses={200: dict})
    @action(detail=False, methods=["post"], url_path="mark-read")
    def mark_as_read(self, request):
        ids = request.data.get("ids")
        return Response({"updated": mark_read(request.user, ids)})


@extend_schema(tags=["notifications"])
class NotificationPreferenceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationPreferenceSerializer

    def get_queryset(self):
        return NotificationPreference.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
