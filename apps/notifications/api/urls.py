from rest_framework.routers import DefaultRouter

from .views import NotificationPreferenceViewSet, NotificationViewSet

app_name = "notifications"

router = DefaultRouter()
router.register("preferences", NotificationPreferenceViewSet, basename="preference")
router.register("", NotificationViewSet, basename="notification")

urlpatterns = router.urls
