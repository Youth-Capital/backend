from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ApplicationViewSet,
    EmployerDashboardView,
    InterviewInviteViewSet,
    InterviewViewSet,
    PlacementViewSet,
    SavedVacancyViewSet,
    VacancyViewSet,
)

app_name = "jobs"

router = DefaultRouter()
router.register("vacancies", VacancyViewSet, basename="vacancy")
router.register("applications", ApplicationViewSet, basename="application")
router.register("interviews", InterviewViewSet, basename="interview")
router.register(
    "interview-invites", InterviewInviteViewSet, basename="interview-invite"
)
router.register("saved", SavedVacancyViewSet, basename="saved-vacancy")
router.register("placements", PlacementViewSet, basename="placement")

urlpatterns = [
    path("employer/dashboard/", EmployerDashboardView.as_view(), name="employer-dashboard"),
    path("", include(router.urls)),
]
