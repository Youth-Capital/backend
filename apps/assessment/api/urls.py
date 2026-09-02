from rest_framework.routers import DefaultRouter

from .views import AttemptViewSet, QuestionViewSet, TestViewSet

app_name = "assessment"

router = DefaultRouter()
router.register("tests", TestViewSet, basename="test")
router.register("attempts", AttemptViewSet, basename="attempt")
router.register("questions", QuestionViewSet, basename="question")

urlpatterns = router.urls
