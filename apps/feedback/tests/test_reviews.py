"""Platform feedback: asking, answering, and reading the result."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.common.enums import ModerationStatus, Role
from apps.feedback.models import (
    CampaignStatus,
    PlatformReview,
    ReviewCampaign,
    ReviewRequest,
    ReviewTrigger,
)
from apps.feedback.services import (
    ASK_COOLDOWN_DAYS,
    MIN_ACCOUNT_AGE_DAYS,
    can_ask,
    launch_campaign,
    request_review,
    review_stats,
    submit_review,
)
from apps.notifications.models import Notification


def _age(user, days):
    """Backdate the account so the age rule stops blocking."""
    user.date_joined = timezone.now() - timedelta(days=days)
    user.save(update_fields=["date_joined"])
    return user


def test_a_trigger_never_breaks_the_thing_that_fired_it(student, admin_user, monkeypatch):
    """An unsendable invitation must not fail a course completion.

    The feedback loop is the least important thing happening at that moment;
    it is not allowed to be the thing that throws.
    """
    from apps.learning.models import Course, Enrollment, EnrollmentStatus

    _age(student, 60)
    monkeypatch.setattr(
        "apps.notifications.services.notify",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("notification down")),
    )
    course = Course.objects.create(
        title="SQL Fundamentals",
        description="",
        author=admin_user,
        status=ModerationStatus.PUBLISHED,
    )

    enrolment = Enrollment.objects.create(
        user=student, course=course, status=EnrollmentStatus.COMPLETED
    )

    assert enrolment.pk is not None
