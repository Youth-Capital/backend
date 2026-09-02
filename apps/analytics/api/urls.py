from django.urls import path

from .views import (
    AdminDashboardView,
    CohortView,
    FunnelView,
    OutcomeView,
    RiskListView,
    SkillDemandView,
    TrackEventView,
)

app_name = "analytics"

urlpatterns = [
    path("dashboard/", AdminDashboardView.as_view(), name="admin-dashboard"),
    path("funnel/", FunnelView.as_view(), name="funnel"),
    path("cohorts/", CohortView.as_view(), name="cohorts"),
    path("risk-list/", RiskListView.as_view(), name="risk-list"),
    path("skill-demand/", SkillDemandView.as_view(), name="skill-demand"),
    path("outcomes/", OutcomeView.as_view(), name="outcomes"),
    path("track/", TrackEventView.as_view(), name="track"),
]
