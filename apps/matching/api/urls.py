from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    MatchWeightProfileViewSet,
    MyMatchesView,
    ProfessionMatchView,
    VacancyMatchView,
)

app_name = "matching"

router = DefaultRouter()
router.register("weight-profiles", MatchWeightProfileViewSet, basename="weight-profile")

urlpatterns = [
    path("my/", MyMatchesView.as_view(), name="my-matches"),
    path("professions/", ProfessionMatchView.as_view(), name="profession-matches"),
    path("vacancy/<uuid:vacancy_id>/", VacancyMatchView.as_view(), name="vacancy-match"),
    path("", include(router.urls)),
]
