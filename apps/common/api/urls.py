from django.urls import path

from .views import HealthView, ReadyView

app_name = "common"

urlpatterns = [
    path("", HealthView.as_view(), name="health"),
    path("ready/", ReadyView.as_view(), name="ready"),
]
