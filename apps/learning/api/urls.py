from rest_framework.routers import DefaultRouter

from .views import (
    CourseMaterialViewSet,
    CourseModuleViewSet,
    CourseViewSet,
    LessonViewSet,
    MyCertificateViewSet,
    MyEnrollmentViewSet,
    MyNoteViewSet,
)

app_name = "learning"

router = DefaultRouter()
router.register("courses", CourseViewSet, basename="course")
router.register("lessons", LessonViewSet, basename="lesson")
router.register("modules", CourseModuleViewSet, basename="course-module")
router.register("materials", CourseMaterialViewSet, basename="course-material")
router.register("my/enrollments", MyEnrollmentViewSet, basename="my-enrollment")
router.register("my/certificates", MyCertificateViewSet, basename="my-certificate")
router.register("my/notes", MyNoteViewSet, basename="my-note")

urlpatterns = router.urls
