"""Streaks and the "you are not alone" counts.

The streak rules are easy to get subtly wrong — a forgiven day, a long gap, a
second action on the same day — and each mistake either inflates the number or
resets someone's month of work.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.common.enums import Role
from apps.idp.company import MIN_VISIBLE, overview as company_overview
from apps.idp.streaks import GRACE_DAYS, overview, record_activity
from apps.profiles.models import StudentProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def learner(django_user_model):
    user = django_user_model.objects.create_user(
        email="streak@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )
    StudentProfile.objects.create(user=user, youth_id="YK-STREAK-1")
    return user


def test_first_action_starts_a_streak(learner):
    record_activity(learner)
    assert overview(learner)["current_days"] == 1
    assert overview(learner)["done_today"] is True


def test_a_second_action_on_the_same_day_does_not_double_count(learner):
    today = timezone.localdate()
    record_activity(learner, on=today)
    record_activity(learner, on=today)
    record_activity(learner, on=today)

    assert overview(learner)["current_days"] == 1


def test_consecutive_days_extend_the_streak(learner):
    today = timezone.localdate()
    for offset in range(4, -1, -1):
        record_activity(learner, on=today - timedelta(days=offset))

    assert overview(learner)["current_days"] == 5
    assert overview(learner)["longest_days"] == 5


def test_one_missed_day_is_forgiven(learner):
    """People have exams and outages. Punishing one bad day teaches them not
    to bother starting again."""
    today = timezone.localdate()
    record_activity(learner, on=today - timedelta(days=3))
    record_activity(learner, on=today - timedelta(days=2))
    # nothing on day -1
    record_activity(learner, on=today)

    assert overview(learner)["current_days"] == 3


def test_a_long_gap_starts_over(learner):
    today = timezone.localdate()
    record_activity(learner, on=today - timedelta(days=30))
    record_activity(learner, on=today)

    assert overview(learner)["current_days"] == 1
    # The record of what they once managed is kept.
    assert overview(learner)["longest_days"] == 1


def test_a_lapsed_streak_reads_as_zero_without_a_nightly_job(learner):
    """`is_alive` is computed on read, so the number is never stale."""
    record_activity(learner, on=timezone.localdate() - timedelta(days=GRACE_DAYS + 5))

    assert overview(learner)["current_days"] == 0


def test_at_risk_flags_a_live_streak_not_yet_secured_today(learner):
    record_activity(learner, on=timezone.localdate() - timedelta(days=1))

    state = overview(learner)
    assert state["at_risk"] is True
    assert state["done_today"] is False


def test_employers_have_no_streak(django_user_model):
    employer = django_user_model.objects.create_user(
        email="hr2@example.com", password="Str0ng!passw0rd", role=Role.EMPLOYER
    )
    assert record_activity(employer) is None


def test_a_learner_with_no_history_gets_a_safe_empty_state(learner):
    state = overview(learner)
    assert state["current_days"] == 0
    assert state["daily_goal"] >= 1


# -- company ---------------------------------------------------------------
def test_small_counts_are_hidden_rather_than_shown(learner, django_user_model):
    """A count below the floor identifies people and reads as discouraging."""
    from apps.taxonomy.models import Profession

    profession = Profession.objects.create(name_uz="Analyst", slug="analyst")
    profile = learner.student_profile
    profile.target_profession = profession
    profile.save()

    # One peer — below MIN_VISIBLE.
    peer = django_user_model.objects.create_user(
        email="peer@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )
    StudentProfile.objects.create(
        user=peer, youth_id="YK-STREAK-2", target_profession=profession
    )

    assert company_overview(learner)["peers"] is None


def test_a_real_crowd_is_shown(learner, django_user_model):
    from apps.taxonomy.models import Profession

    profession = Profession.objects.create(name_uz="Analyst", slug="analyst")
    profile = learner.student_profile
    profile.target_profession = profession
    profile.save()

    for index in range(MIN_VISIBLE):
        peer = django_user_model.objects.create_user(
            email=f"peer{index}@example.com",
            password="Str0ng!passw0rd",
            role=Role.STUDENT,
        )
        StudentProfile.objects.create(
            user=peer, youth_id=f"YK-P-{index}", target_profession=profession
        )

    peers = company_overview(learner)["peers"]
    assert peers["count"] == MIN_VISIBLE
    assert peers["profession"] == "Analyst"


def test_company_never_returns_names(learner, django_user_model):
    from apps.taxonomy.models import Profession

    profession = Profession.objects.create(name_uz="Analyst", slug="analyst")
    profile = learner.student_profile
    profile.target_profession = profession
    profile.save()
    for index in range(MIN_VISIBLE):
        peer = django_user_model.objects.create_user(
            email=f"named{index}@example.com",
            password="Str0ng!passw0rd",
            role=Role.STUDENT,
        )
        StudentProfile.objects.create(
            user=peer, youth_id=f"YK-N-{index}", first_name="Dilnoza",
            target_profession=profession,
        )

    assert "Dilnoza" not in str(company_overview(learner))
