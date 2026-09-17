from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AIAssistantView,
    AssistantPreferencesView,
    AIMonitoringView,
    AIProviderConfigViewSet,
    AIRequestLogViewSet,
    CapabilityView,
    ChatView,
    IntakeView,
    RecommendationViewSet,
    SafetyEventViewSet,
)

app_name = "ai"

router = DefaultRouter()
router.register("recommendations", RecommendationViewSet, basename="recommendation")
router.register("providers", AIProviderConfigViewSet, basename="ai-provider")
router.register("logs", AIRequestLogViewSet, basename="ai-log")
router.register("safety-events", SafetyEventViewSet, basename="ai-safety-event")

urlpatterns = [
    path("chat/", ChatView.as_view(), name="chat"),
    path("intake/", IntakeView.as_view(), name="intake"),
    path("assistant/", AIAssistantView.as_view(), name="assistant"),
    path(
        "assistant/preferences/",
        AssistantPreferencesView.as_view(),
        name="assistant-preferences",
    ),
    path("capability/", CapabilityView.as_view(), name="capability"),
    path("monitoring/", AIMonitoringView.as_view(), name="monitoring"),
    path("", include(router.urls)),
]
