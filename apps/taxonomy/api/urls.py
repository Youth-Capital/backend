from rest_framework.routers import DefaultRouter

from .views import (
    CapitalDimensionViewSet,
    ProfessionViewSet,
    RegionViewSet,
    SkillCategoryViewSet,
    SkillDimensionViewSet,
    SkillViewSet,
)

app_name = "taxonomy"

router = DefaultRouter()
router.register("regions", RegionViewSet, basename="region")
router.register("skill-categories", SkillCategoryViewSet, basename="skill-category")
router.register("skills", SkillViewSet, basename="skill")
router.register("capital-dimensions", CapitalDimensionViewSet, basename="capital-dimension")
router.register("skill-dimensions", SkillDimensionViewSet, basename="skill-dimension")
router.register("professions", ProfessionViewSet, basename="profession")

urlpatterns = router.urls
