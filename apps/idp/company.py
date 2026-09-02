"""“You are not the only one.”

Isolation is one of the most-cited reasons learners abandon online courses,
and this platform had no social surface at all: no groups, no classmates, no
sign that anyone else was walking the same road.

Full social features — discussions, cohorts, groups — are a large build. This
is the cheap part that carries most of the effect: counts of real people doing
the same thing, drawn from rows that already exist.

Two rules keep it honest:

* **No names, ever.** These are aggregates. Naming who else is preparing for a
  profession would expose a learner's ambitions to strangers.
* **Never invent a number.** A count below the floor is not shown at all. A
  fabricated "join 500 others" is found out in a week, and a small true number
  is only embarrassing if you print it — silence is not a lie.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

#: Below this, a count identifies people rather than describing a crowd, and
#: reads as discouraging besides. Say nothing instead.
MIN_VISIBLE = 3

RECENT_DAYS = 7


def _at_least(value: int) -> int | None:
    return value if value >= MIN_VISIBLE else None


def peers_on_same_profession(user) -> dict | None:
    """How many others are aiming at the same profession."""
    from apps.profiles.models import StudentProfile

    profile = getattr(user, "student_profile", None)
    target = getattr(profile, "target_profession", None) if profile else None
    if target is None:
        return None

    count = (
        StudentProfile.objects.filter(target_profession=target)
        .exclude(user_id=user.id)
        .count()
    )
    visible = _at_least(count)
    return None if visible is None else {"count": visible, "profession": target.name}


def recent_test_takers(user) -> dict | None:
    """How many people passed a test this week — proof the bar is clearable."""
    from apps.assessment.models import TestAttempt

    since = timezone.now() - timedelta(days=RECENT_DAYS)
    count = (
        TestAttempt.objects.filter(submitted_at__gte=since, passed=True)
        .exclude(user_id=user.id)
        .values("user_id")
        .distinct()
        .count()
    )
    visible = _at_least(count)
    return None if visible is None else {"count": visible, "days": RECENT_DAYS}


def learners_on_course(course, user=None) -> int | None:
    """Enrolment count for one course, or nothing if too small to show."""
    from apps.learning.models import Enrollment

    query = Enrollment.objects.filter(course=course)
    if user is not None:
        query = query.exclude(user_id=user.id)
    return _at_least(query.count())


def hired_from_profession(user) -> dict | None:
    """People who reached employment from the same target.

    The strongest signal available: not "others are studying" but "others
    finished". Only counted where the outcome was actually recorded.
    """
    from apps.jobs.models import Application

    profile = getattr(user, "student_profile", None)
    target = getattr(profile, "target_profession", None) if profile else None
    if target is None:
        return None

    count = (
        Application.objects.filter(
            status="ACCEPTED", student__student_profile__target_profession=target
        )
        .exclude(student_id=user.id)
        .values("student_id")
        .distinct()
        .count()
    )
    visible = _at_least(count)
    return None if visible is None else {"count": visible, "profession": target.name}


def overview(user) -> dict:
    """Everything true enough to show. Absent keys mean "not enough people yet"."""
    return {
        "peers": peers_on_same_profession(user),
        "recent_test_takers": recent_test_takers(user),
        "hired": hired_from_profession(user),
    }
