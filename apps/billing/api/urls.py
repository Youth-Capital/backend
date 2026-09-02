from django.urls import path

from .views import (
    CancelView,
    CheckoutView,
    ConfirmView,
    FeatureCheckView,
    MySubscriptionView,
    PaymentHistoryView,
    PlanListView,
    UsageView,
)

app_name = "billing"

urlpatterns = [
    path("plans/", PlanListView.as_view(), name="plans"),
    path("subscription/", MySubscriptionView.as_view(), name="subscription"),
    path("usage/", UsageView.as_view(), name="usage"),
    path("features/<str:feature>/", FeatureCheckView.as_view(), name="feature-check"),
    path("checkout/", CheckoutView.as_view(), name="checkout"),
    path("confirm/", ConfirmView.as_view(), name="confirm"),
    path("cancel/", CancelView.as_view(), name="cancel"),
    path("history/", PaymentHistoryView.as_view(), name="history"),
]
