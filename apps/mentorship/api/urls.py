from rest_framework.routers import DefaultRouter

from .views import (
    MentorDashboardViewSet,
    MentorDirectoryViewSet,
    MentorSessionViewSet,
    PlanReviewViewSet,
)

app_name = "mentorship"

router = DefaultRouter()
router.register("mentors", MentorDirectoryViewSet, basename="mentor")
router.register("sessions", MentorSessionViewSet, basename="mentor-session")
router.register("plan-reviews", PlanReviewViewSet, basename="plan-review")
router.register("dashboard", MentorDashboardViewSet, basename="mentor-dashboard")

urlpatterns = router.urls
