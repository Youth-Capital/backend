from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CVViewSet,
    MyPublicProfileView,
    PortfolioViewSet,
    PublicPassportView,
)

app_name = "cv"

router = DefaultRouter()
router.register("documents", CVViewSet, basename="cv-document")
router.register("portfolio", PortfolioViewSet, basename="portfolio-item")

urlpatterns = [
    path("passport/", MyPublicProfileView.as_view(), name="my-passport"),
    path("passport/<slug:slug>/", PublicPassportView.as_view(), name="public-passport"),
    path("", include(router.urls)),
]
