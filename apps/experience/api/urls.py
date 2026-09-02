from rest_framework.routers import DefaultRouter

from .views import ExperienceViewSet

app_name = "experience"

router = DefaultRouter()
router.register("", ExperienceViewSet, basename="experience")

urlpatterns = router.urls
