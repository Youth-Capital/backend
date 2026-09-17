from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    DismissRequestView,
    MyReviewView,
    PublicReviewsView,
    ReviewAdminViewSet,
    ReviewCampaignViewSet,
)

app_name = "feedback"

router = DefaultRouter()
router.register("reviews", ReviewAdminViewSet, basename="platform-review")
router.register("campaigns", ReviewCampaignViewSet, basename="review-campaign")

urlpatterns = [
    path("me/", MyReviewView.as_view(), name="my-review"),
    path(
        "requests/<uuid:request_id>/dismiss/",
        DismissRequestView.as_view(),
        name="dismiss-request",
    ),
    path("public/", PublicReviewsView.as_view(), name="public-reviews"),
    path("", include(router.urls)),
]
