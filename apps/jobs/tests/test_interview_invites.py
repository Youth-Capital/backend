"""Inviting a candidate to talk.

Almost nobody in a candidate list has applied — that is what talent search is
for — so "invite them to an interview" cannot mean "schedule an interview".
An `Interview` hangs off an `Application`, and an application is the student's
own act. Creating one for them would put a decision they never made into the
funnel and hand their name to the employer without their say.

So the invitation is a separate object the student answers, and these tests
pin the three things that follow from that:

* accepting is what creates the application, and it is recorded as having come
  from the employer rather than as a fresh applicant;
* declining creates nothing at all;
* a candidate who *had* already applied skips the invitation entirely, because
  there is nothing left to ask.

The last block is a security regression. Scheduling an interview took an
application id from the request body and never checked whose it was, so one
company could book a meeting on a rival's application — and the rival's
candidate would be notified about it.
"""

import pytest
from django.utils import timezone

from apps.common.enums import EvidenceSource

pytestmark = pytest.mark.django_db

INVITES_URL = "/api/v1/jobs/interview-invites/"
INTERVIEWS_URL = "/api/v1/jobs/interviews/"


@pytest.fixture
def candidate(db, student, taxonomy, give_skill):
    give_skill(student, taxonomy["sql"], 80, EvidenceSource.TEST)
    give_skill(student, taxonomy["power_bi"], 60, EvidenceSource.COURSE)
    return student


@pytest.fixture
def matched(db, candidate, vacancy):
    from apps.matching.services import compute_and_store_match

    return compute_and_store_match(candidate, vacancy)


def soon():
    return (timezone.now() + timezone.timedelta(days=7)).isoformat()


def invite_payload(vacancy, candidate, **extra):
    payload = {
        "vacancy": str(vacancy.id),
        "student": str(candidate.id),
        "message": "Здравствуйте! Хотим поговорить о позиции.",
        "proposed_at": soon(),
        "mode": "ONLINE",
    }
    payload.update(extra)
    return payload


# -- the invitation --------------------------------------------------------
def test_an_employer_can_invite_a_candidate_who_never_applied(
    auth, employer, vacancy, candidate, matched
):
    response = auth(employer).post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )

    assert response.status_code == 201, response.data
    assert response.data["kind"] == "invite"
    assert response.data["invite"]["status"] == "PENDING"


def test_the_invitation_creates_no_application_by_itself(
    auth, employer, vacancy, candidate, matched
):
    """The employer's wish is not the student's decision."""
    from apps.jobs.models import Application

    auth(employer).post(INVITES_URL, invite_payload(vacancy, candidate), format="json")

    assert not Application.objects.filter(student=candidate, vacancy=vacancy).exists()


def test_the_candidate_is_notified(auth, employer, vacancy, candidate, matched):
    from apps.notifications.models import Notification

    auth(employer).post(INVITES_URL, invite_payload(vacancy, candidate), format="json")

    assert Notification.objects.filter(
        user=candidate, ref_type="InterviewInvite"
    ).exists()


def test_the_same_candidate_cannot_be_invited_twice_while_they_think(
    auth, employer, vacancy, candidate, matched
):
    client = auth(employer)
    client.post(INVITES_URL, invite_payload(vacancy, candidate), format="json")

    second = client.post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )

    assert second.status_code == 409, second.data


def test_an_interview_cannot_be_proposed_in_the_past(
    auth, employer, vacancy, candidate, matched
):
    past = (timezone.now() - timezone.timedelta(days=1)).isoformat()

    response = auth(employer).post(
        INVITES_URL, invite_payload(vacancy, candidate, proposed_at=past), format="json"
    )

    assert response.status_code == 400, response.data


# -- the answer ------------------------------------------------------------
def test_accepting_creates_the_application_and_books_the_interview(
    auth, employer, vacancy, candidate, matched
):
    from apps.jobs.models import Application, ApplicationStatus, Interview

    created = auth(employer).post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )
    invite_id = created.data["invite"]["id"]

    response = auth(candidate).post(
        f"{INVITES_URL}{invite_id}/respond/", {"accept": True}, format="json"
    )

    assert response.status_code == 200, response.data
    application = Application.objects.get(student=candidate, vacancy=vacancy)
    assert application.status == ApplicationStatus.INTERVIEW
    assert Interview.objects.filter(application=application).exists()


def test_an_accepted_invitation_says_where_the_application_came_from(
    auth, employer, vacancy, candidate, matched
):
    """The funnel must not report a sourced candidate as a walk-in."""
    from apps.jobs.models import Application, ApplicationEvent, ApplicationStatus

    created = auth(employer).post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )
    auth(candidate).post(
        f"{INVITES_URL}{created.data['invite']['id']}/respond/",
        {"accept": True},
        format="json",
    )

    application = Application.objects.get(student=candidate, vacancy=vacancy)
    first = ApplicationEvent.objects.filter(application=application).first()
    assert first.from_status == "", "it was entered as a transition, not an origin"
    assert first.to_status == ApplicationStatus.SHORTLISTED
    assert "invitation" in first.note.lower()


def test_declining_creates_nothing(auth, employer, vacancy, candidate, matched):
    from apps.jobs.models import Application

    created = auth(employer).post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )

    response = auth(candidate).post(
        f"{INVITES_URL}{created.data['invite']['id']}/respond/",
        {"accept": False, "note": "Спасибо, сейчас не ищу."},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["invite"]["status"] == "DECLINED"
    assert not Application.objects.filter(student=candidate, vacancy=vacancy).exists()


def test_an_invitation_can_only_be_answered_once(
    auth, employer, vacancy, candidate, matched
):
    created = auth(employer).post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )
    url = f"{INVITES_URL}{created.data['invite']['id']}/respond/"
    auth(candidate).post(url, {"accept": True}, format="json")

    again = auth(candidate).post(url, {"accept": False}, format="json")

    assert again.status_code == 409


def test_somebody_elses_invitation_cannot_be_answered(
    auth, employer, vacancy, candidate, other_student, matched
):
    created = auth(employer).post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )

    response = auth(other_student).post(
        f"{INVITES_URL}{created.data['invite']['id']}/respond/",
        {"accept": True},
        format="json",
    )

    assert response.status_code in {403, 404}


def test_a_student_only_sees_invitations_addressed_to_them(
    auth, employer, vacancy, candidate, other_student, matched
):
    auth(employer).post(INVITES_URL, invite_payload(vacancy, candidate), format="json")

    response = auth(other_student).get(INVITES_URL)

    assert response.data["results"] == []


# -- the candidate who had already applied ---------------------------------
def test_an_existing_applicant_is_booked_without_being_asked(
    auth, employer, vacancy, candidate, matched
):
    """Nothing to invite them to: they are already in the process."""
    from apps.jobs.models import Application, ApplicationStatus, Interview

    Application.objects.create(student=candidate, vacancy=vacancy)

    response = auth(employer).post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )

    assert response.status_code == 201, response.data
    assert response.data["kind"] == "scheduled"
    application = Application.objects.get(student=candidate, vacancy=vacancy)
    assert application.status == ApplicationStatus.INTERVIEW
    assert Interview.objects.filter(application=application).exists()


# -- reachability and ownership --------------------------------------------
def test_another_companys_vacancy_cannot_be_used_to_invite(
    auth, other_employer, vacancy, candidate, matched
):
    response = auth(other_employer).post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )

    assert response.status_code in {403, 404}


def test_somebody_who_is_not_a_candidate_cannot_be_invited(
    auth, employer, vacancy, other_student
):
    """Otherwise the endpoint is a way to message any user on the platform."""
    response = auth(employer).post(
        INVITES_URL, invite_payload(vacancy, other_student), format="json"
    )

    assert response.status_code in {403, 404}


def test_a_student_cannot_invite_anyone(auth, student, vacancy, candidate, matched):
    response = auth(student).post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )

    assert response.status_code in {403, 404}


# -- the scheduling hole this file also closes ------------------------------
def test_a_rival_cannot_schedule_an_interview_on_your_application(
    auth, employer, other_employer, vacancy, candidate, matched
):
    """The regression.

    The interview queryset scoped reads only. Creation took an application id
    straight from the body, so a rival could book a meeting on somebody else's
    application — and the candidate would be notified about a conversation the
    company they applied to never arranged.
    """
    from apps.jobs.models import Application, Interview

    application = Application.objects.create(student=candidate, vacancy=vacancy)

    response = auth(other_employer).post(
        INTERVIEWS_URL,
        {
            "application": str(application.id),
            "scheduled_at": soon(),
            "duration_minutes": 30,
            "mode": "ONLINE",
        },
        format="json",
    )

    assert response.status_code in {400, 403, 404}, response.data
    assert not Interview.objects.filter(application=application).exists()


def test_the_owner_can_still_schedule_an_interview(
    auth, employer, vacancy, candidate, matched
):
    """Closing the hole must not close the door."""
    from apps.jobs.models import Application, Interview

    application = Application.objects.create(student=candidate, vacancy=vacancy)

    response = auth(employer).post(
        INTERVIEWS_URL,
        {
            "application": str(application.id),
            "scheduled_at": soon(),
            "duration_minutes": 30,
            "mode": "ONLINE",
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert Interview.objects.filter(application=application).exists()


# -- the student must hear about it -----------------------------------------
#
# An interview the candidate is not told about is not an interview. Each path
# that books one is checked separately, because they book it from different
# places and it would be easy to wire the notification into only one.
def test_booking_an_interview_notifies_the_candidate(
    auth, employer, vacancy, candidate, matched
):
    """The employer schedules; the student is told."""
    from apps.jobs.models import Application
    from apps.notifications.models import Notification

    application = Application.objects.create(student=candidate, vacancy=vacancy)
    before = Notification.objects.filter(
        user=candidate, type="INTERVIEW_SCHEDULED"
    ).count()

    response = auth(employer).post(
        INTERVIEWS_URL,
        {
            "application": str(application.id),
            "scheduled_at": soon(),
            "duration_minutes": 45,
            "mode": "ONLINE",
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert (
        Notification.objects.filter(
            user=candidate, type="INTERVIEW_SCHEDULED"
        ).count()
        == before + 1
    )


def test_the_notification_carries_what_the_sentence_needs(
    auth, employer, vacancy, candidate, matched
):
    """The body is a template; a missing value renders as a hole in a sentence."""
    from apps.jobs.models import Application
    from apps.notifications.models import Notification

    application = Application.objects.create(student=candidate, vacancy=vacancy)
    auth(employer).post(
        INTERVIEWS_URL,
        {
            "application": str(application.id),
            "scheduled_at": soon(),
            "mode": "ONLINE",
        },
        format="json",
    )

    notification = Notification.objects.filter(
        user=candidate, type="INTERVIEW_SCHEDULED"
    ).latest("created_at")

    assert notification.body_key == "notifications.interview.scheduled.body"
    assert notification.payload.get("vacancy") == vacancy.title
    assert notification.payload.get("scheduled_at"), "no time in the message"
    assert notification.action_url.endswith(str(application.id))


def test_booking_from_the_candidate_page_also_notifies(
    auth, employer, vacancy, candidate, matched
):
    """The other entry point: the button on the candidate card."""
    from apps.jobs.models import Application
    from apps.notifications.models import Notification

    Application.objects.create(student=candidate, vacancy=vacancy)
    before = Notification.objects.filter(
        user=candidate, type="INTERVIEW_SCHEDULED"
    ).count()

    response = auth(employer).post(
        INVITES_URL, invite_payload(vacancy, candidate), format="json"
    )

    assert response.data["kind"] == "scheduled"
    assert (
        Notification.objects.filter(
            user=candidate, type="INTERVIEW_SCHEDULED"
        ).count()
        == before + 1
    )


def test_booking_an_applicant_without_a_time_is_refused(
    auth, employer, vacancy, candidate, matched
):
    """The bug this test exists for.

    An invitation may be timeless — "we would like to talk" is a real message.
    A booking may not: the candidate has already applied, so nobody is left to
    agree a time with. Without a time nothing was created, and the employer was
    told the interview had been scheduled.
    """
    from apps.jobs.models import Application, Interview

    Application.objects.create(student=candidate, vacancy=vacancy)

    response = auth(employer).post(
        INVITES_URL,
        invite_payload(vacancy, candidate, proposed_at=None),
        format="json",
    )

    assert response.status_code == 400, response.data
    assert not Interview.objects.filter(application__student=candidate).exists()


def test_an_invitation_without_a_time_is_still_allowed(
    auth, employer, vacancy, candidate, matched
):
    """The other side of that rule: asking does not need a calendar."""
    response = auth(employer).post(
        INVITES_URL,
        invite_payload(vacancy, candidate, proposed_at=None),
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["kind"] == "invite"
    assert response.data["invite"]["proposed_at"] is None
