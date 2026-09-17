"""Root URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

api_v1 = [
    path("health/", include("apps.common.api.urls")),
    path("auth/", include("apps.accounts.api.urls")),
    path("taxonomy/", include("apps.taxonomy.api.urls")),
    path("me/", include("apps.profiles.api.urls")),
    path("learning/", include("apps.learning.api.urls")),
    path("assessment/", include("apps.assessment.api.urls")),
    path("knowledge/", include("apps.knowledge.api.urls")),
    path("capital/", include("apps.capital.api.urls")),
    path("plan/", include("apps.idp.api.urls")),
    path("experience/", include("apps.experience.api.urls")),
    path("cv/", include("apps.cv.api.urls")),
    path("jobs/", include("apps.jobs.api.urls")),
    path("matching/", include("apps.matching.api.urls")),
    path("ai/", include("apps.ai.api.urls")),
    path("notifications/", include("apps.notifications.api.urls")),
    path("analytics/", include("apps.analytics.api.urls")),
    path("billing/", include("apps.billing.api.urls")),
    path("audit/", include("apps.audit.api.urls")),
    path("feedback/", include("apps.feedback.api.urls")),
]

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/v1/", include((api_v1, "v1"))),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/v1/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/v1/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = settings.PLATFORM_NAME
admin.site.site_title = settings.PLATFORM_NAME
admin.site.index_title = "Platform administration"
