from django.urls import path

from .views import CapitalHistoryView, CapitalOverviewView, CapitalRecomputeView

app_name = "capital"

urlpatterns = [
    path("overview/", CapitalOverviewView.as_view(), name="overview"),
    path("history/", CapitalHistoryView.as_view(), name="history"),
    path("recompute/", CapitalRecomputeView.as_view(), name="recompute"),
]
