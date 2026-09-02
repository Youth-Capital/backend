from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    MyEducationViewSet,
    MyProfileView,
    MySkillsViewSet,
    OnboardingView,
    PublicProfileView,
    StudentDashboardView,
)

app_name = "profiles"

router = DefaultRouter()
router.register("skills", MySkillsViewSet, basename="my-skill")
router.register("education", MyEducationViewSet, basename="my-education")

urlpatterns = [
    path("profile/", MyProfileView.as_view(), name="my-profile"),
    path("onboarding/", OnboardingView.as_view(), name="onboarding"),
    path("dashboard/", StudentDashboardView.as_view(), name="student-dashboard"),
    path("users/<uuid:user_id>/", PublicProfileView.as_view(), name="public-profile"),
    path("", include(router.urls)),
]
