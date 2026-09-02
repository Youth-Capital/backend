from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import DevelopmentPlanViewSet, GoalViewSet, PlanSummaryView, TaskViewSet

app_name = "idp"

router = DefaultRouter()
router.register("goals", GoalViewSet, basename="goal")
router.register("plans", DevelopmentPlanViewSet, basename="plan")
router.register("tasks", TaskViewSet, basename="task")

urlpatterns = [
    path("summary/", PlanSummaryView.as_view(), name="summary"),
    path("", include(router.urls)),
]
