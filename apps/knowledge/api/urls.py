from django.urls import path

from .views import KnowledgeHistoryView, KnowledgeOverviewView, KnowledgeRecomputeView

app_name = "knowledge"

urlpatterns = [
    path("overview/", KnowledgeOverviewView.as_view(), name="overview"),
    path("history/", KnowledgeHistoryView.as_view(), name="history"),
    path("recompute/", KnowledgeRecomputeView.as_view(), name="recompute"),
]
